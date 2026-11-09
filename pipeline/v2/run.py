"""CLI for pipeline v2: python -m pipeline.v2.run (spec 2026-11-09-session-pipeline-v2 §5.1).

    python -m pipeline.v2.run --from-transcript path/to/2026-11-05-x.txt --profile sandbox
    python -m pipeline.v2.run --video path/to/video.mp4 --dry-run

Same flags and meanings as pipeline/run.py, without --dir. Never import pipeline.run: it loads .env and parses argv.
This module imports only the standard library at the top: main() loads the profile env and runs dry_run.activate
before anything that reads paths at import (pipeline/run.py:46-102).
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import List, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RUN_SUFFIX = "langgraph"  # the console's hardcoded layout, pipeline/ui/runs.py


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python -m pipeline.v2.run")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--video", help="Path to a single video file")
    src.add_argument("--from-transcript", help="Path to a transcript .txt: skips Whisper, starts at extract")
    ap.add_argument("--profile", choices=["prod", "sandbox"], default="prod", help="Env profile (default: prod)")
    ap.add_argument("--run-id", default=None, help="Override run id (default: timestamp)")
    ap.add_argument("--language", default=None, help="Code spoken in the video, e.g. es (default: config/publishing.json)")
    ap.add_argument("--dry-run", action="store_true",
                    help="Run everything but keep nothing: scratch vault and audit DB in the run folder, sandbox Notion.")
    ap.add_argument("--processed-root", type=Path, default=PROJECT_ROOT / "processed",
                    help="Root of per-run output dirs (default: <project>/processed)")
    ap.add_argument("--reminder-interval", type=int, default=1800,
                    help="Seconds between Telegram reminders while waiting on a reply (default 1800)")
    ap.add_argument("--auto-approve", action="store_true",
                    help="TEST ONLY: reply 'yes' to approvals and abort fast on errors.")
    ap.add_argument("--telegram-on-error", action="store_true",
                    help="With --auto-approve, post the error prompt to Telegram before aborting.")
    return ap


def _state_root() -> Path:
    raw = os.environ.get("PAINFORWISDOM_STATE_ROOT", "").strip()
    return Path(raw).expanduser().resolve() if raw else PROJECT_ROOT


def bootstrap(args: argparse.Namespace, run_id: str) -> None:
    """Profile env, then dry_run.activate, then the audit DB path (pipeline/run.py:46-100; Q3915, Q3917)."""
    from dotenv import load_dotenv

    state_root = _state_root()
    load_dotenv(state_root / (".env.sandbox" if args.profile == "sandbox" else ".env"))
    os.environ["PIPELINE_PROFILE"] = args.profile
    if args.profile == "sandbox":
        os.environ["TELEGRAM_MESSAGE_PREFIX"] = "[SANDBOX] "
    if args.dry_run:
        from pipeline import dry_run

        run_dir = Path(args.processed_root) / run_id / RUN_SUFFIX
        try:
            dry_run.activate(run_dir, state_root)
        except dry_run.DryRunError as exc:
            raise SystemExit(str(exc)) from None
        os.environ["PIPELINE_V2_DB"] = str(run_dir.parent / "dry-run" / "v2.db")
    elif args.profile == "sandbox" and not os.environ.get("PIPELINE_V2_DB"):
        os.environ["PIPELINE_V2_DB"] = str(PROJECT_ROOT / "pipeline" / "v2-sandbox.db")


def _git_sha() -> Optional[str]:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=PROJECT_ROOT, capture_output=True, text=True,
                              timeout=10).stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def process(args: argparse.Namespace, run_id: str) -> int:
    """One run. 0 on PASS or PARTIAL, 2 on FAIL (pipeline/run.py:_process_one_video). Call after bootstrap()."""
    # Path-reading modules: imported only now, after bootstrap().
    from pipeline import dry_run
    from pipeline.graph import _error_text
    from pipeline.publishing_config import run_language
    from pipeline.run_common import _build_initial_state, _write_run_record
    from pipeline.runtime import date_from_filename
    from pipeline.telegram import send as telegram_send
    from pipeline.v2.orchestrator import Orchestrator
    from pipeline.v2.store import Store, _file_hash

    run_dir = Path(args.processed_root) / run_id / RUN_SUFFIX
    run_dir.mkdir(parents=True, exist_ok=True)
    video = Path(args.video).resolve() if args.video else None
    source = "video" if video else "transcript"
    input_path = video or Path(args.from_transcript).resolve()
    print(f"[v2] run_id={run_id} run_dir={run_dir} input={input_path.name}")
    t_start = time.time()

    if dry_run.active():
        dry_run.prepare_vault(date_from_filename(input_path.name))
    language = run_language(args.language)
    _write_run_record(
        run_dir, run_id=run_id, status="running", verdict=None, pid=os.getpid(), profile=args.profile,
        input={"kind": source, "path": str(input_path)}, language=language, started_at=t_start, ended_at=None,
        hitl_wait_s=0.0, error=None, engine="sessions", **({"dry_run": True} if dry_run.active() else {}),
    )
    store = Store()
    store.start_run(run_id, profile=args.profile, dry_run=dry_run.active(), input_kind=source,
                    input_path=str(input_path), input_sha256=_file_hash(str(input_path))[0], language=language,
                    git_sha=_git_sha(), started_at=t_start)
    orch = Orchestrator(run_id, run_dir, store, auto_approve=args.auto_approve,
                        telegram_on_error=args.telegram_on_error, reminder_interval=args.reminder_interval)
    try:
        rc = telegram_send(f"📥 Pipeline intake (v2 sessions) — {source}: `{input_path.name}`\n"
                           f"profile: {args.profile}\nrun_id: {run_id}")
        if rc != 0:
            print(f"[v2] telegram intake notify rc={rc} (non-fatal)")
        initial, start_at = _build_initial_state(args, video, run_id, run_dir)
        final = orch.walk(initial, start_at)
    except BaseException as exc:
        _write_run_record(run_dir, status="failed", ended_at=time.time(), error=_error_text(exc))
        store.end_run(run_id, "failed", error=_error_text(exc))
        raise

    verdict = final.get("validator_verdict", "?")
    verdict = None if verdict == "?" else verdict
    _write_run_record(run_dir, status="done", verdict=verdict, ended_at=time.time(), hitl_wait_s=orch.hitl_wait)
    store.end_run(run_id, "done", verdict=verdict)
    print(f"\n[v2] DONE net={time.time() - t_start - orch.hitl_wait:.1f}s verdict={verdict}\n[v2] run_dir={run_dir}")
    return 0 if verdict in ("PASS", "PARTIAL") else 2


def main(argv: Optional[List[str]] = None) -> int:
    args = _parser().parse_args(argv)
    run_id = args.run_id or time.strftime("%Y-%m-%d_%H%M%S")
    if args.video and not Path(args.video).is_file():
        raise SystemExit(f"video not found: {Path(args.video).resolve()}")
    bootstrap(args, run_id)
    try:
        return process(args, run_id)
    except Exception as exc:  # noqa: BLE001
        print(f"[v2] FATAL: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
