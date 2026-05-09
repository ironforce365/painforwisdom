"""Stage 1 — transcribe video via extract_transcription.sh (local Whisper).

Reused as-is from the legacy pipeline. We just shell out, locate the produced
file, read it, assert non-empty, and store path + text in state.
"""
from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
from typing import Any, Dict

from pipeline.contracts import assert_inputs
from pipeline.runtime import (
    EXTRACT_TRANSCRIPTION,
    PROJECT_ROOT,
    append_metric,
    date_from_filename,
    run_telemetry_path,
)
from pipeline.state import State


def node_transcribe(state: State) -> Dict[str, Any]:
    assert_inputs("transcribe", state)
    t0 = time.time()
    video = state["video_path"]
    run_dir = state["run_dir"]
    date = date_from_filename(video)
    print(f"[transcribe] start video={os.path.basename(video)} date={date}")

    proc = subprocess.run(
        [str(EXTRACT_TRANSCRIPTION), video, "English", date],
        capture_output=True,
        text=True,
        cwd=str(PROJECT_ROOT),
    )
    if proc.returncode != 0:
        print(proc.stdout)
        print(proc.stderr, flush=True)
        raise RuntimeError(f"extract_transcription.sh exit {proc.returncode}")

    out_dir = Path(video).parent / "auto-generated"
    candidates = sorted(
        out_dir.glob(f"transcript_{date}*.txt"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        raise RuntimeError(f"transcript not found in {out_dir} for date={date}")
    transcript_path = candidates[0]
    transcript_text = transcript_path.read_text()

    word_count = len(transcript_text.split())
    if word_count < 30:
        raise RuntimeError(
            f"transcript suspiciously short ({word_count} words) — "
            f"likely a Whisper failure on {transcript_path.name}"
        )

    duration = time.time() - t0
    append_metric(
        run_telemetry_path(run_dir),
        "transcribe",
        duration_s=round(duration, 2),
        transcript_path=str(transcript_path),
        transcript_words=word_count,
    )
    print(f"[transcribe] done {duration:.1f}s words={word_count}")
    return {
        "transcript_path": str(transcript_path),
        "transcript_text": transcript_text,
        "transcript_word_count": word_count,
        "video_date": date,
    }
