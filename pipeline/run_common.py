"""Run helpers shared by pipeline.run (v1) and pipeline.v2.run, moved verbatim from pipeline/run.py.

Import only after the profile env and dry_run.activate: publishing_config and runtime read paths at import.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from pipeline.contracts import InputContractError
from pipeline.publishing_config import run_language
from pipeline.runtime import date_from_filename


def _format_proposal(payload: Dict[str, Any]) -> str:
    return (
        f"⚠️ Pipeline pause — {payload.get('kind','?')} approval needed\n\n"
        f"{payload.get('proposal','')}\n\n"
        "Reply to resume. Pipeline will wait."
    )


def _classify_non_recoverable(exc: Exception) -> Optional[str]:
    """Return a short human-readable reason if `exc` cannot be fixed by a retry.

    Recovery via `retry` only helps for transient failures (network blip,
    rate-limit window passing). The cases below will fail identically on a
    re-run, so we surface that to the user instead of inviting a futile retry.
    """
    if isinstance(exc, InputContractError):
        return "input contract violation — retry would hit the same check"
    msg = str(exc).lower()
    if "extra usage is required for long context requests" in msg:
        return (
            "Anthropic long-context tier rejected the request — "
            "subscription lacks the long-context billing add-on, retry will fail identically"
        )
    if "invalid authentication credentials" in msg or "unauthorized" in msg:
        return "auth credentials rejected after in-call refresh — retry would re-fail"
    return None


def _format_error_prompt(
    stage: str,
    exc: Exception,
    non_recoverable_reason: Optional[str] = None,
) -> str:
    msg = f"{type(exc).__name__}: {exc}"
    if len(msg) > 800:
        msg = msg[:800] + " …(truncated)"
    if non_recoverable_reason:
        return (
            f"🚫 Pipeline error in `{stage}` — NON-RECOVERABLE\n\n"
            f"Reason: {non_recoverable_reason}\n\n"
            f"{msg}\n\n"
            "Reply `abort` to quarantine this video. `retry` will not help."
        )
    return (
        f"⚠️ Pipeline error in `{stage}`\n\n"
        f"{msg}\n\n"
        "Reply: `retry` (re-run from checkpoint) | `abort` (quarantine this video)"
    )


_APPROVAL_TITLES = {
    "theme": "New theme: approval needed",
    "framework": "New framework: approval needed",
    "curator": "Curator: decision needed",
}


MAX_AUTO_APPROVALS = 10


def _refuse_unattended_curator(payload: Dict[str, Any], rounds: int = 0) -> None:
    """--auto-approve answers `yes` forever: an action only a human can settle, or a proposer that never stops, would loop on the LLM."""
    if payload.get("kind") == "curator":
        raise RuntimeError(f"kb-curator needs a decision, --auto-approve cannot give one: {payload.get('proposal', '')}")
    if rounds > MAX_AUTO_APPROVALS:
        raise RuntimeError(f"--auto-approve gave up after {MAX_AUTO_APPROVALS} approvals: the node keeps asking")


# --- Run record (C2): docs/superpowers/specs/2026-10-31-pipeline-console-ui.md section 2


def _write_run_record(run_dir: Path, **fields: Any) -> None:
    """Merge `fields` into <run_dir>/run.json, replacing the file atomically."""
    path = run_dir / "run.json"
    try:
        record = json.loads(path.read_text())
    except (FileNotFoundError, ValueError):
        record = {}
    record.update(fields)
    tmp = path.with_name("run.json.tmp")
    tmp.write_text(json.dumps(record))
    os.replace(tmp, path)


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
                "language": run_language(args.language),
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
            "language": run_language(args.language),
            "run_id": run_id,
            "run_dir": str(run_dir),
            "metrics": [],
        },
        "transcribe",
    )
