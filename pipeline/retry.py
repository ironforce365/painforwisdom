"""Resume failed pipeline runs from their LangGraph checkpoint.

Use case: a video moved to ``bulk/quarantine/`` because a downstream stage
raised (Notion outage, Notion schema mismatch, OpenCV missing — whatever).
The work already done (transcribe, extract, vault entry, blog post, …)
has been checkpointed by ``SqliteSaver``. Re-running ``pipeline.run`` from
scratch would burn that work over again. ``pipeline.retry`` instead
invokes the existing graph thread with ``None`` so LangGraph picks up at
the last successful node and re-runs only the failed one + everything
downstream of it.

Discovery modes (mutually exclusive):

  - ``--run-id <id>``         resume a specific thread/run-id directly.
  - ``--video <path>``        resume the run whose checkpoint state's
                              ``video_path`` basename matches the file.
  - default                   scan ``bulk/quarantine/`` (or
                              ``--quarantine <dir>``), match each video
                              to its run-id, retry them all.

Run-level behaviour mirrors ``pipeline.run``:

  - On success (validator PASS / PARTIAL), move the video out of
    quarantine into ``processed/<run_id>/source/`` so the run directory
    mirrors a normal successful run.
  - On failure, leave the video in quarantine and continue with the next.
  - HITL approvals (kb_curator) and per-node error prompts still escalate
    to Telegram unless ``--auto-approve`` is passed.

Profiles:
  --profile prod (default) loads ``.env`` and the prod checkpoint DB.
  --profile sandbox loads ``.env.sandbox`` and the sandbox checkpoint DB.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _peek_profile(argv: List[str]) -> str:
    for i, a in enumerate(argv):
        if a == "--profile" and i + 1 < len(argv):
            return argv[i + 1]
        if a.startswith("--profile="):
            return a.split("=", 1)[1]
    return "prod"


_PROFILE = _peek_profile(sys.argv[1:])
_ENV_FILE = ".env.sandbox" if _PROFILE == "sandbox" else ".env"

from dotenv import load_dotenv  # noqa: E402

load_dotenv(PROJECT_ROOT / _ENV_FILE)

import os  # noqa: E402

if _PROFILE == "sandbox":
    os.environ["TELEGRAM_MESSAGE_PREFIX"] = "[SANDBOX] "

from langgraph.types import Command  # noqa: E402

from pipeline.contracts import InputContractError  # noqa: E402
from pipeline.graph import build_graph  # noqa: E402
from pipeline.run import (  # noqa: E402
    VIDEO_EXTENSIONS,
    _ask_indefinitely,
    _format_error_prompt,
    _format_proposal,
    _get_pending_interrupt,
    _last_failed_node,
    _move_into,
)
from pipeline.telegram import send as telegram_send  # noqa: E402


PROCESSED_ROOT = PROJECT_ROOT / "processed"
DEFAULT_QUARANTINE = PROJECT_ROOT / "bulk" / "quarantine"


# --- Checkpoint inspection --------------------------------------------------


def _candidate_run_ids() -> List[str]:
    """Return run_ids that look like failed runs (no ``source/`` archive).

    Sorted most-recent-first so the discovery loop finds the latest match
    first when two runs both touched the same video.
    """
    if not PROCESSED_ROOT.is_dir():
        return []
    ids: List[str] = []
    for entry in PROCESSED_ROOT.iterdir():
        if not entry.is_dir():
            continue
        if entry.name.startswith("_"):
            continue
        if (entry / "source").is_dir():
            # Successful run — already archived. Skip.
            continue
        ids.append(entry.name)
    ids.sort(reverse=True)
    return ids


def _state_for(graph, run_id: str) -> Optional[Dict[str, Any]]:
    """Read the latest checkpoint state for a thread, or None if not stored."""
    try:
        snap = graph.get_state({"configurable": {"thread_id": run_id}})
    except Exception as exc:  # noqa: BLE001
        print(f"   [retry] WARN get_state failed for {run_id}: {exc}")
        return None
    values = snap.values or {}
    return dict(values) if values else None


def _is_terminal(graph, run_id: str) -> bool:
    """True when the checkpoint shows no remaining nodes to run.

    Used to skip threads that already completed (would no-op on resume).
    """
    try:
        snap = graph.get_state({"configurable": {"thread_id": run_id}})
    except Exception:  # noqa: BLE001
        return False
    return not snap.next


def _find_run_id_for_video(graph, video_path: Path) -> Optional[str]:
    target = video_path.name
    for run_id in _candidate_run_ids():
        state = _state_for(graph, run_id)
        if not state:
            continue
        vp = state.get("video_path") or ""
        tp = state.get("transcript_path") or ""
        if vp and Path(vp).name == target:
            return run_id
        # Whisper writes the transcript next to the video; match on stem
        # as a fallback when the video_path got rewritten by archival.
        if tp and Path(tp).stem.endswith(video_path.stem):
            return run_id
    return None


def _video_for_run_id(graph, run_id: str) -> Optional[Path]:
    state = _state_for(graph, run_id)
    if not state:
        return None
    vp = state.get("video_path") or ""
    if vp:
        return Path(vp)
    return None


# --- Resume driver ----------------------------------------------------------


def _resume_graph(
    graph,
    config: Dict[str, Any],
    args: argparse.Namespace,
) -> Tuple[Dict[str, Any], float]:
    """Drive the graph from its checkpoint to completion.

    Mirrors ``pipeline.run._drive_graph`` but starts with ``invoke(None)``
    (= resume from last checkpoint) instead of ``invoke(initial)``. Keeps
    the same HITL approval + error-recovery semantics.
    """
    pending: Optional[Any] = None  # None = resume
    rounds = 0
    last_state: Dict[str, Any] = {}
    hitl_wait = 0.0

    while True:
        try:
            last_state = graph.invoke(pending, config=config)
            pending = None
        except (InputContractError, Exception) as exc:  # noqa: BLE001
            stage = _last_failed_node(graph, config)
            print(f"[retry] pipeline raised in {stage}: {type(exc).__name__}: {exc}")
            if args.auto_approve:
                if args.telegram_on_error:
                    rc = telegram_send(_format_error_prompt(stage, exc))
                    if rc != 0:
                        print(f"[retry] telegram-on-error send rc={rc} (non-fatal)")
                raise
            prompt = _format_error_prompt(stage, exc)
            t_wait = time.time()
            reply = _ask_indefinitely(prompt, args.reminder_interval).strip().lower()
            hitl_wait += time.time() - t_wait
            print(f"[retry] error-recovery reply: {reply!r}")
            if reply.startswith("retry"):
                pending = None
                continue
            raise

        payload = _get_pending_interrupt(graph, config)
        if payload is None:
            return last_state, hitl_wait
        rounds += 1
        print(f"[retry] HITL interrupt #{rounds}: {payload.get('kind')}")
        if args.auto_approve:
            reply = "yes"
            print("[retry] --auto-approve: bypassing Telegram, resuming with 'yes'")
        else:
            t_wait = time.time()
            reply = _ask_indefinitely(_format_proposal(payload), args.reminder_interval)
            hitl_wait += time.time() - t_wait
        print(f"[retry] resume reply: {reply!r}")
        pending = Command(resume=reply)  # type: ignore[assignment]


# --- One-target retry -------------------------------------------------------


def _retry_one(
    graph,
    run_id: str,
    *,
    video: Optional[Path],
    args: argparse.Namespace,
) -> int:
    """Resume one run. Returns 0 on PASS/PARTIAL, 2 on FAIL.

    On success, archives the source video into
    ``processed/<run_id>/source/`` so the run directory looks like a
    normal completed batch entry.
    """
    config = {"configurable": {"thread_id": run_id}}
    print(f"\n[retry] === run_id={run_id} ===")

    state = _state_for(graph, run_id)
    if state is None:
        print(f"[retry] no checkpoint state for thread {run_id!r}; nothing to resume.")
        return 2

    if _is_terminal(graph, run_id):
        verdict = state.get("validator_verdict", "?")
        print(f"[retry] checkpoint already terminal (verdict={verdict}); nothing to do.")
        # Still archive the video if it's sitting in quarantine.
        if video and video.is_file() and verdict in {"PASS", "PARTIAL"}:
            archive = PROCESSED_ROOT / run_id / "source"
            _move_into(video, archive)
            print(f"[retry] archived stale quarantine video → {archive / video.name}")
        return 0 if verdict in {"PASS", "PARTIAL"} else 2

    next_nodes = list(getattr(graph.get_state(config), "next", []) or [])
    print(f"[retry] resuming at next={next_nodes or '?'}")
    rc = telegram_send(
        f"🔁 Pipeline retry — run_id `{run_id}`\n"
        f"next: {', '.join(next_nodes) or '?'}\n"
        f"video: `{video.name if video else state.get('video_path','?')}`"
    )
    if rc != 0:
        print(f"[retry] telegram intake notify rc={rc} (non-fatal)")

    if args.dry_run:
        print(f"[retry] --dry-run: would resume from {next_nodes}; not invoking graph.")
        return 0

    t_start = time.time()
    try:
        result, hitl_wait = _resume_graph(graph, config, args)
    except Exception as exc:  # noqa: BLE001
        print(f"[retry] resume aborted: {type(exc).__name__}: {exc}")
        return 2

    total = time.time() - t_start - hitl_wait
    verdict = result.get("validator_verdict", "?")
    print(f"[retry] DONE total={total:.1f}s verdict={verdict} run_id={run_id}")

    if verdict in {"PASS", "PARTIAL"} and video and video.is_file():
        archive = PROCESSED_ROOT / run_id / "source"
        _move_into(video, archive)
        print(f"[retry] archived quarantine video → {archive / video.name}")
    return 0 if verdict in {"PASS", "PARTIAL"} else 2


# --- Discovery driver -------------------------------------------------------


def _discover_targets(
    graph,
    *,
    args: argparse.Namespace,
) -> List[Tuple[str, Optional[Path]]]:
    """Build the work list: pairs of (run_id, video_path_or_None)."""
    targets: List[Tuple[str, Optional[Path]]] = []

    if args.run_id:
        targets.append((args.run_id, _video_for_run_id(graph, args.run_id)))
        return targets

    if args.video:
        video_path = Path(args.video).resolve()
        if not video_path.is_file():
            raise SystemExit(f"video not found: {video_path}")
        rid = _find_run_id_for_video(graph, video_path)
        if rid is None:
            raise SystemExit(
                f"no checkpoint thread found whose state matches {video_path.name}. "
                "Pass --run-id explicitly if you know it."
            )
        targets.append((rid, video_path))
        return targets

    quarantine_dir = Path(args.quarantine).resolve()
    if not quarantine_dir.is_dir():
        print(f"[retry] quarantine dir not found: {quarantine_dir} (nothing to retry).")
        return []
    videos = [
        p for p in sorted(quarantine_dir.iterdir())
        if p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS
    ]
    if not videos:
        print(f"[retry] no videos in {quarantine_dir}.")
        return []

    for video in videos:
        rid = _find_run_id_for_video(graph, video)
        if rid is None:
            print(
                f"[retry] WARN no checkpoint thread for {video.name}; "
                "skipping (run pipeline.run --video … to start fresh)."
            )
            continue
        targets.append((rid, video))
    return targets


# --- Entry point ------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--run-id", default=None, help="Specific thread/run id to resume.")
    src.add_argument("--video", default=None, help="Specific quarantined video to resume.")
    ap.add_argument(
        "--quarantine",
        default=str(DEFAULT_QUARANTINE),
        help="Directory to scan for quarantined videos (default: bulk/quarantine).",
    )
    ap.add_argument("--profile", choices=["prod", "sandbox"], default="prod")
    ap.add_argument(
        "--run-suffix",
        default="langgraph",
        help="Subdir under processed/<run_id>/ (default: langgraph). Mirrors pipeline.run.",
    )
    ap.add_argument(
        "--reminder-interval",
        type=int,
        default=1800,
        help="Seconds between Telegram reminder re-posts (default 1800).",
    )
    ap.add_argument(
        "--auto-approve",
        action="store_true",
        help="Auto-reply 'yes' to HITL prompts and abort fast on errors. TEST ONLY.",
    )
    ap.add_argument(
        "--telegram-on-error",
        action="store_true",
        help="Post error prompt to Telegram before re-raising (--auto-approve only).",
    )
    ap.add_argument("--dry-run", action="store_true", help="Discover targets, do not resume.")
    args = ap.parse_args()

    graph, saver = build_graph(start_at="transcribe")
    try:
        targets = _discover_targets(graph, args=args)
        if not targets:
            return 0

        print(f"[retry] discovered {len(targets)} target(s):")
        for run_id, video in targets:
            print(f"   - {run_id}  ({video.name if video else 'no video archive'})")

        successes = 0
        failures = 0
        for run_id, video in targets:
            rc = _retry_one(graph, run_id, video=video, args=args)
            if rc == 0:
                successes += 1
            else:
                failures += 1
        print(
            f"\n[retry] summary: {successes} ok, {failures} failed, {len(targets)} total"
        )
        telegram_send(
            f"🔁 Pipeline retry summary: {successes} ok / {failures} failed "
            f"({len(targets)} attempted)"
        )
        return 0 if failures == 0 else 2
    finally:
        try:
            saver.conn.close()  # type: ignore[attr-defined]
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
