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
    through ``pipeline.hitl`` (console mailbox or Telegram) unless
    ``--auto-approve`` is passed.
  - A run with a ``run.json`` keeps it true (status, pid, final verdict).

Profiles:
  --profile prod (default) loads ``.env`` and the prod checkpoint DB.
  --profile sandbox loads ``.env.sandbox`` and the sandbox checkpoint DB.
"""
from __future__ import annotations

import argparse
import json
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

# .env lives in the state root (PAINFORWISDOM_STATE_ROOT): the tag-pinned live
# checkout has none. Mirrors pipeline.runtime.state_root(), which cannot be
# imported yet because it reads VAULT_PATH at import time.
import os as _os  # noqa: E402

_STATE_ROOT = (
    Path(_os.environ["PAINFORWISDOM_STATE_ROOT"].strip()).expanduser().resolve()
    if _os.environ.get("PAINFORWISDOM_STATE_ROOT", "").strip()
    else PROJECT_ROOT
)
load_dotenv(_STATE_ROOT / _ENV_FILE)

import os  # noqa: E402

os.environ["PIPELINE_PROFILE"] = _PROFILE  # nodes read it through pipeline.publishing_config
if _PROFILE == "sandbox":
    os.environ["TELEGRAM_MESSAGE_PREFIX"] = "[SANDBOX] "

from pipeline import hitl  # noqa: E402
from pipeline.contracts import InputContractError  # noqa: E402
from pipeline.graph import _error_text, build_graph  # noqa: E402
from pipeline.run import (  # noqa: E402
    VIDEO_EXTENSIONS,
    _answer_interrupt,
    _approval_request,
    _classify_non_recoverable,
    _format_error_prompt,
    _get_pending_interrupt,
    _last_failed_node,
    _move_into,
    _pending_work,
    _refuse_unattended_curator,
    _require_finished,
    _write_run_record,
)
from pipeline.telegram import send as telegram_send  # noqa: E402


PROCESSED_ROOT = PROJECT_ROOT / "processed"
DEFAULT_QUARANTINE = PROJECT_ROOT / "bulk" / "quarantine"


# --- Checkpoint inspection --------------------------------------------------


def _candidate_run_ids(root: Path = PROCESSED_ROOT) -> List[str]:
    """Return run_ids that look like failed runs (no ``source/`` archive).

    Sorted most-recent-first so the discovery loop finds the latest match
    first when two runs both touched the same video.
    """
    if not root.is_dir():
        return []
    ids: List[str] = []
    for entry in root.iterdir():
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
        return not _pending_work(graph, {"configurable": {"thread_id": run_id}})
    except Exception:  # noqa: BLE001
        return False


def _find_run_id_for_video(graph, video_path: Path, root: Path = PROCESSED_ROOT) -> Optional[str]:
    target = video_path.name
    for run_id in _candidate_run_ids(root):
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


# --- Run record -------------------------------------------------------------


def _run_dir(args: argparse.Namespace, run_id: str) -> Path:
    return Path(args.processed_root) / run_id / args.run_suffix


def _update_record(run_dir: Path, **fields: Any) -> None:
    """Only a run that has a run.json: a legacy run reads from runs.jsonl, and a stub record would hide it."""
    if (run_dir / "run.json").is_file():
        _write_run_record(run_dir, **fields)


def _prior_hitl_wait(run_dir: Path) -> float:
    try:
        return float(json.loads((run_dir / "run.json").read_text()).get("hitl_wait_s") or 0.0)
    except (OSError, ValueError):
        return 0.0


# --- Resume driver ----------------------------------------------------------


def _resume_graph(
    graph,
    config: Dict[str, Any],
    args: argparse.Namespace,
    run_dir: Path,
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
    prior_wait = _prior_hitl_wait(run_dir)

    while True:
        try:
            last_state = graph.invoke(pending, config=config)
            pending = None
        except (InputContractError, Exception) as exc:  # noqa: BLE001
            stage = _last_failed_node(graph, config)
            print(f"[retry] pipeline raised in {stage}: {type(exc).__name__}: {exc}")
            non_recoverable = _classify_non_recoverable(exc)
            prompt = _format_error_prompt(stage, exc, non_recoverable)
            if args.auto_approve:
                if args.telegram_on_error:
                    rc = telegram_send(prompt)
                    if rc != 0:
                        print(f"[retry] telegram-on-error send rc={rc} (non-fatal)")
                raise
            t_wait = time.time()
            _update_record(run_dir, status="waiting_reply")
            reply = hitl.ask(run_dir, hitl.error_request(stage, _error_text(exc), prompt, non_recoverable),
                             args.reminder_interval)
            hitl_wait += time.time() - t_wait
            _update_record(run_dir, status="running", hitl_wait_s=prior_wait + hitl_wait)
            reply = (reply or "").strip().lower()
            print(f"[retry] error-recovery reply: {reply!r}")
            if reply.startswith("retry") and not non_recoverable:
                pending = None
                continue
            raise

        payload = _get_pending_interrupt(graph, config)
        if payload is None:
            _require_finished(graph, config)
            return last_state, hitl_wait
        rounds += 1
        print(f"[retry] HITL interrupt #{rounds}: {payload.get('kind')}")
        if args.auto_approve:
            _refuse_unattended_curator(payload, rounds)
            reply = "yes"
            print("[retry] --auto-approve: bypassing Telegram, resuming with 'yes'")
        else:
            t_wait = time.time()
            _update_record(run_dir, status="waiting_hitl")
            reply = hitl.ask(run_dir, _approval_request(graph, config, payload), args.reminder_interval)
            hitl_wait += time.time() - t_wait
            _update_record(run_dir, status="running", hitl_wait_s=prior_wait + hitl_wait)
        print(f"[retry] resume reply: {reply!r}")
        _answer_interrupt(graph, config, reply)
        pending = None


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
    run_dir = _run_dir(args, run_id)
    source_dir = Path(args.processed_root) / run_id / "source"

    state = _state_for(graph, run_id)
    if state is None:
        print(f"[retry] no checkpoint state for thread {run_id!r}; nothing to resume.")
        if not args.dry_run:
            _update_record(run_dir, status="failed", ended_at=time.time(),
                           error=f"no checkpoint state for run {run_id}: restart it from scratch")
        return 2

    if _is_terminal(graph, run_id):
        verdict = state.get("validator_verdict", "?")
        print(f"[retry] checkpoint already terminal (verdict={verdict}); nothing to do.")
        if not args.dry_run:
            _update_record(run_dir, status="done", verdict=None if verdict == "?" else verdict,
                           ended_at=time.time(), error=None)
        # Still archive the video if it's sitting in quarantine.
        if video and video.is_file() and verdict in {"PASS", "PARTIAL"} and not args.no_archive:
            _move_into(video, source_dir)
            print(f"[retry] archived stale quarantine video → {source_dir / video.name}")
        return 0 if verdict in {"PASS", "PARTIAL"} else 2

    next_nodes = _pending_work(graph, config)
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
    _update_record(run_dir, status="running", verdict=None, pid=os.getpid(), ended_at=None, error=None)
    try:
        result, hitl_wait = _resume_graph(graph, config, args, run_dir)
    except Exception as exc:  # noqa: BLE001
        print(f"[retry] resume aborted: {type(exc).__name__}: {exc}")
        _update_record(run_dir, status="failed", ended_at=time.time(), error=_error_text(exc))
        return 2
    except BaseException as exc:
        _update_record(run_dir, status="failed", ended_at=time.time(), error=_error_text(exc))
        raise

    total = time.time() - t_start - hitl_wait
    verdict = result.get("validator_verdict", "?")
    print(f"[retry] DONE total={total:.1f}s verdict={verdict} run_id={run_id}")
    _update_record(run_dir, status="done", verdict=None if verdict == "?" else verdict, ended_at=time.time(), error=None)

    if verdict in {"PASS", "PARTIAL"} and video and video.is_file() and not args.no_archive:
        _move_into(video, source_dir)
        print(f"[retry] archived quarantine video → {source_dir / video.name}")
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
        rid = _find_run_id_for_video(graph, video_path, Path(args.processed_root))
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
        rid = _find_run_id_for_video(graph, video, Path(args.processed_root))
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
        "--processed-root", type=Path, default=PROCESSED_ROOT,
        help="Root of per-run output dirs (default: <project>/processed). Mirrors pipeline.run.",
    )
    ap.add_argument(
        "--no-archive", action="store_true",
        help="Leave the video where it is on success. The console passes it: its inputs and packages point at the file.",
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
