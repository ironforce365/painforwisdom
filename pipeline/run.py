"""CLI orchestrator for the LangGraph content pipeline.

Usage:
    python -m pipeline.run --video path/to/video.mp4
    python -m pipeline.run --dir path/to/videos/
    python -m pipeline.run --from-transcript path/to/transcript.txt   # test/sandbox

Three input sources, mutually exclusive:
- ``--video <path>`` — single video, full pipeline (Whisper → ... → Notion).
- ``--dir <path>`` — directory of videos, processed sequentially. After each
  video, success → ``<dir>/processed/``, failure → ``<dir>/quarantine/``.
- ``--from-transcript <path>`` — feed a checked-in transcript file directly
  into the ``extract`` stage. Skips Whisper. Used for sandbox smoke tests.

Profile selection:
- ``--profile prod`` (default) — loads ``.env``.
- ``--profile sandbox`` — loads ``.env.sandbox`` (separate Notion DBs +
  separate Obsidian vault dir + checkpoint DB) and prefixes every Telegram
  message with ``[SANDBOX]``.

HITL handling:
- kb_curator's ``interrupt()`` (theme/framework approval) goes through
  Telegram via ``_ask_indefinitely``. The pipeline waits forever, re-posting
  the prompt every ``--reminder-interval`` seconds.
- Persistent node failures (after LangGraph's RetryPolicy has exhausted
  transient retries) escalate to Telegram with a ``retry / abort`` ask.
"""
from __future__ import annotations

import argparse
import shutil
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# --- Profile-aware env loading ----------------------------------------------
# We must `load_dotenv` BEFORE importing pipeline.* (which read VAULT_PATH,
# Notion DB IDs, CHECKPOINT_DB_PATH at import time). To know which env file
# to load we have to peek at sys.argv before argparse runs — argparse needs
# the full module already imported, which is too late.

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _peek_profile(argv: List[str]) -> str:
    """Best-effort scan of argv for --profile <value> | --profile=<value>."""
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

# Sandbox messages get tagged so the user can mute / ignore them.
import os  # noqa: E402

if _PROFILE == "sandbox":
    os.environ["TELEGRAM_MESSAGE_PREFIX"] = "[SANDBOX] "

# Heavy imports must come after env load.
from langgraph.types import Command  # noqa: E402

from pipeline.contracts import InputContractError  # noqa: E402
from pipeline.graph import build_graph  # noqa: E402
from pipeline.runtime import date_from_filename  # noqa: E402
from pipeline.telegram import ask as telegram_ask  # noqa: E402
from pipeline.telegram import send as telegram_send  # noqa: E402

VIDEO_EXTENSIONS = (".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi")


# --- HITL prompts -----------------------------------------------------------


def _format_proposal(payload: Dict[str, Any]) -> str:
    return (
        f"⚠️ Pipeline pause — {payload.get('kind','?')} approval needed\n\n"
        f"{payload.get('proposal','')}\n\n"
        "Reply to resume. Pipeline will wait."
    )


def _format_error_prompt(stage: str, exc: Exception) -> str:
    msg = f"{type(exc).__name__}: {exc}"
    if len(msg) > 800:
        msg = msg[:800] + " …(truncated)"
    return (
        f"⚠️ Pipeline error in `{stage}`\n\n"
        f"{msg}\n\n"
        "Reply: `retry` (re-run from checkpoint) | `abort` (quarantine this video)"
    )


def _get_pending_interrupt(graph, config) -> Optional[Dict[str, Any]]:
    snap = graph.get_state(config)
    if not snap.next:
        return None
    for task in snap.tasks:
        for intr in task.interrupts:
            return intr.value if hasattr(intr, "value") else intr
    return None


def _ask_indefinitely(prompt: str, reminder_interval: int) -> str:
    """Post `prompt` and wait forever for a Telegram reply, re-posting every
    `reminder_interval` seconds (the re-post serves as the reminder)."""
    waited = 0
    while True:
        reply = telegram_ask(prompt, timeout_seconds=reminder_interval)
        if reply:
            return reply
        waited += reminder_interval
        print(f"[run] no reply after {waited // 60} min — re-posting prompt as reminder")


def _last_failed_node(graph, config) -> str:
    """Best-effort: find the node name LangGraph stopped on."""
    try:
        snap = graph.get_state(config)
        if snap.next:
            return ",".join(snap.next)
        for task in snap.tasks:
            if task.error:
                return task.name
    except Exception:  # noqa: BLE001
        pass
    return "?"


# --- Per-video orchestration ------------------------------------------------


def _discover_videos(directory: Path) -> List[Path]:
    out: List[Path] = []
    for entry in directory.iterdir():
        if entry.is_file() and entry.suffix.lower() in VIDEO_EXTENSIONS:
            out.append(entry)
    out.sort()
    return out


def _move_into(source: Path, dest_dir: Path) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = dest_dir / source.name
    if target.exists():
        target = dest_dir / f"{source.stem}_{int(time.time())}{source.suffix}"
    shutil.move(str(source), str(target))
    return target


def _build_initial_state(args: argparse.Namespace, video: Optional[Path], run_id: str, run_dir: Path) -> Tuple[Dict[str, Any], str]:
    """Return (initial_state, start_at)."""
    if args.from_transcript:
        transcript_path = Path(args.from_transcript).resolve()
        if not transcript_path.is_file():
            raise SystemExit(f"transcript not found: {transcript_path}")
        text = transcript_path.read_text()
        word_count = len(text.split())
        video_date = date_from_filename(str(transcript_path))
        return (
            {
                "video_path": "",
                "transcript_path": str(transcript_path),
                "transcript_text": text,
                "transcript_word_count": word_count,
                "video_date": video_date,
                "run_id": run_id,
                "run_dir": str(run_dir),
                "metrics": [],
            },
            "extract",
        )
    assert video is not None
    return (
        {
            "video_path": str(video),
            "run_id": run_id,
            "run_dir": str(run_dir),
            "metrics": [],
        },
        "transcribe",
    )


def _drive_graph(
    graph,
    initial: Dict[str, Any],
    config: Dict[str, Any],
    args: argparse.Namespace,
) -> Tuple[Dict[str, Any], float]:
    """Invoke the graph and handle BOTH HITL approvals and post-retry failures.

    Returns ``(final_state, hitl_wait_seconds)`` — the final state dict from
    the last successful invoke plus the cumulative seconds spent blocked on
    Telegram replies (HITL approvals + error-recovery prompts), so the caller
    can report net work time excluding human wait.
    """
    pending: Optional[Dict[str, Any]] = initial  # first call
    rounds = 0
    last_state: Dict[str, Any] = {}
    hitl_wait = 0.0

    while True:
        try:
            if pending is initial:
                last_state = graph.invoke(initial, config=config)
                pending = None
            else:
                # subsequent calls are resumes (HITL or post-error retry)
                last_state = graph.invoke(pending, config=config)
                pending = None
        except (InputContractError, Exception) as exc:  # noqa: BLE001
            stage = _last_failed_node(graph, config)
            print(f"[run] pipeline raised in {stage}: {type(exc).__name__}: {exc}")
            if args.auto_approve:
                # In tests, abort fast rather than block forever waiting for
                # a Telegram reply that no one will send.
                if args.telegram_on_error:
                    # Non-blocking: surface the error to Telegram so smoke
                    # failures are visible on phone, but still re-raise so
                    # the test exits fast instead of waiting for a reply.
                    rc = telegram_send(_format_error_prompt(stage, exc))
                    if rc != 0:
                        print(f"[run] telegram-on-error send rc={rc} (non-fatal)")
                raise
            prompt = _format_error_prompt(stage, exc)
            t_wait = time.time()
            reply = _ask_indefinitely(prompt, args.reminder_interval).strip().lower()
            hitl_wait += time.time() - t_wait
            print(f"[run] error-recovery reply: {reply!r}")
            if reply.startswith("retry"):
                # Re-invoke with `None` — LangGraph resumes from the last
                # successful checkpoint and re-runs the failed node.
                pending = None
                last_state = graph.invoke(None, config=config)
                continue
            # abort / anything else
            raise

        # Check for HITL interrupts (kb_curator approval).
        payload = _get_pending_interrupt(graph, config)
        if payload is None:
            return last_state, hitl_wait
        rounds += 1
        print(f"[run] HITL interrupt #{rounds}: {payload.get('kind')}")
        if args.auto_approve:
            reply = "yes"
            print("[run] --auto-approve: bypassing Telegram, resuming with 'yes'")
        else:
            t_wait = time.time()
            reply = _ask_indefinitely(_format_proposal(payload), args.reminder_interval)
            hitl_wait += time.time() - t_wait
        print(f"[run] resume reply: {reply!r}")
        pending = Command(resume=reply)  # type: ignore[assignment]


def _process_one_video(
    args: argparse.Namespace,
    run_id: str,
    *,
    video: Optional[Path] = None,
) -> int:
    """Returns exit code: 0 on PASS or PARTIAL, 2 on FAIL."""
    run_dir = PROJECT_ROOT / "processed" / run_id / args.run_suffix
    run_dir.mkdir(parents=True, exist_ok=True)

    label = video.name if video else Path(args.from_transcript).name
    print(f"[run] run_id={run_id} run_dir={run_dir} input={label}")
    t_start = time.time()

    source = "video" if video else "transcript"
    intake_msg = (
        f"📥 Pipeline intake — {source}: `{label}`\n"
        f"profile: {args.profile}\n"
        f"run_id: {run_id}"
    )
    rc = telegram_send(intake_msg)
    if rc != 0:
        print(f"[run] telegram intake notify rc={rc} (non-fatal)")

    initial, start_at = _build_initial_state(args, video, run_id, run_dir)
    graph, saver = build_graph(start_at=start_at)
    try:
        config = {"configurable": {"thread_id": run_id}}
        result, hitl_wait = _drive_graph(graph, initial, config, args)

        total = time.time() - t_start - hitl_wait
        verdict = result.get("validator_verdict", "?")
        print()
        print(f"[run] DONE total={total:.1f}s target={args.target_seconds}s verdict={verdict}")
        print(f"[run] run_dir={run_dir}")
        if verdict == "PASS" or verdict == "PARTIAL":
            return 0
        return 2
    finally:
        try:
            saver.conn.close()  # type: ignore[attr-defined]
        except Exception:
            pass


# --- Batch + single ---------------------------------------------------------


def _run_batch(args: argparse.Namespace) -> int:
    videos_dir = Path(args.dir).resolve()
    if not videos_dir.is_dir():
        raise SystemExit(f"dir not found: {videos_dir}")

    videos = _discover_videos(videos_dir)
    if not videos:
        raise SystemExit(f"no videos in {videos_dir}")

    processed_dir = videos_dir / "processed"
    quarantine_dir = videos_dir / "quarantine"

    print(f"[batch] {len(videos)} videos in {videos_dir}")
    successes: List[str] = []
    failures: List[Tuple[str, str]] = []

    for idx, video in enumerate(videos, start=1):
        # Always suffix with idx so two videos started in the same wall-clock
        # second can never share a run_id (= same checkpoint thread, same
        # run_dir). This was a real collision risk before.
        base = args.run_id or time.strftime("%Y-%m-%d_%H%M%S")
        run_id = f"{base}_{idx:03d}"
        print(f"\n[batch] ({idx}/{len(videos)}) {video.name}")

        rc = 1
        err_msg = ""
        try:
            rc = _process_one_video(args, run_id, video=video)
        except Exception as exc:  # noqa: BLE001
            err_msg = f"{type(exc).__name__}: {exc}"
            print(f"[batch] EXCEPTION on {video.name}: {err_msg}")

        if rc == 0:
            moved = _move_into(video, processed_dir)
            successes.append(video.name)
            print(f"[batch] OK → {moved}")
        else:
            moved = _move_into(video, quarantine_dir)
            reason = err_msg or f"verdict not PASS (rc={rc})"
            failures.append((video.name, reason))
            print(f"[batch] QUARANTINE → {moved} ({reason})")

    lines = [
        f"📦 Batch done: {len(successes)} ok / {len(failures)} failed",
        f"dir: {videos_dir}",
    ]
    if failures:
        lines.append("Failures:")
        for name, reason in failures:
            lines.append(f"  • {name} — {reason}")
    summary = "\n".join(lines)
    print()
    print(summary)
    rc = telegram_send(summary)
    if rc != 0:
        print(f"[batch] telegram send rc={rc}, retrying once", file=sys.stderr)
        time.sleep(2.0)
        telegram_send(summary)
    return 0 if not failures else 2


def main() -> int:
    ap = argparse.ArgumentParser()
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--video", help="Path to a single video file")
    src.add_argument("--dir", help="Path to a directory of videos (sequential batch)")
    src.add_argument("--from-transcript", help="Path to a transcript .txt — skips Whisper, runs extract→...→validator (test mode)")
    ap.add_argument("--profile", choices=["prod", "sandbox"], default="prod", help="Env profile (default: prod)")
    ap.add_argument("--run-id", default=None, help="Override run id base (default: timestamp)")
    ap.add_argument(
        "--run-suffix", default="langgraph", help="Subdir under processed/<run_id>/ (default: langgraph)"
    )
    ap.add_argument("--target-seconds", type=int, default=600, help="Soft target wall-clock (default 600 = 10 min)")
    ap.add_argument(
        "--reminder-interval",
        type=int,
        default=1800,
        help="Seconds between Telegram reminder re-posts while waiting on HITL or error reply (default 1800 = 30 min). Pipeline waits forever.",
    )
    ap.add_argument(
        "--auto-approve",
        action="store_true",
        help="Auto-reply 'yes' to every HITL approval prompt and abort fast on errors. "
             "TEST ONLY — bypasses Telegram round-trip. Production runs must NOT use this.",
    )
    ap.add_argument(
        "--telegram-on-error",
        action="store_true",
        help="When set with --auto-approve, post the error prompt to Telegram "
             "(non-blocking) before re-raising. Lets smoke runs surface failures on "
             "phone without blocking on a reply.",
    )
    args = ap.parse_args()

    if args.dir:
        return _run_batch(args)

    if args.video:
        video = Path(args.video).resolve()
        if not video.is_file():
            raise SystemExit(f"video not found: {video}")
        run_id = args.run_id or time.strftime("%Y-%m-%d_%H%M%S")
        try:
            return _process_one_video(args, run_id, video=video)
        except Exception as exc:  # noqa: BLE001
            print(f"[run] FATAL: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 2

    # --from-transcript
    run_id = args.run_id or time.strftime("%Y-%m-%d_%H%M%S")
    try:
        return _process_one_video(args, run_id, video=None)
    except Exception as exc:  # noqa: BLE001
        print(f"[run] FATAL: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
