"""Stage 1 — transcribe video via extract_transcription.sh (local Whisper).

Reused as-is from the legacy pipeline. We just shell out, locate the produced
file, read it, assert non-empty, and store path + text in state.

CUDA OOM handling: if the local Whisper run fails with a CUDA out-of-memory
error (the GPU is shared with other processes and `medium` still doesn't fit),
we transparently retry once on CPU by re-invoking the shell helper with
``WHISPER_DEVICE=cpu``. The fallback is ~10× slower but never fails for memory
reasons. We notify Telegram so the user knows wall-clock will balloon.
"""
from __future__ import annotations

import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, Optional

from pipeline.contracts import assert_inputs
from pipeline.runtime import (
    EXTRACT_TRANSCRIPTION,
    PROJECT_ROOT,
    append_metric,
    date_from_filename,
    run_telemetry_path,
)
from pipeline.state import State
from pipeline.telegram import send as telegram_send

_OOM_RE = re.compile(
    r"(CUDA out of memory|OutOfMemoryError|torch\.cuda\.OutOfMemoryError)"
)


def _run_whisper_shell(video: str, date: str, env_extra: Dict[str, str]) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env.update(env_extra)
    return subprocess.run(
        [str(EXTRACT_TRANSCRIPTION), video, "English", date],
        capture_output=True,
        text=True,
        cwd=str(PROJECT_ROOT),
        env=env,
    )


def node_transcribe(state: State) -> Dict[str, Any]:
    assert_inputs("transcribe", state)
    t0 = time.time()
    video = state["video_path"]
    run_dir = state["run_dir"]
    date = date_from_filename(video)
    print(f"[transcribe] start video={os.path.basename(video)} date={date}")

    proc = _run_whisper_shell(video, date, env_extra={})
    fallback: Optional[str] = None
    if proc.returncode != 0:
        combined = (proc.stderr or "") + (proc.stdout or "")
        if _OOM_RE.search(combined):
            print("[transcribe] CUDA OOM detected — retrying on CPU")
            try:
                telegram_send(
                    "⚠️ Whisper hit CUDA OOM — retrying on CPU. "
                    "Wall-clock will be ~10× slower than GPU."
                )
            except Exception:  # noqa: BLE001
                pass
            proc = _run_whisper_shell(video, date, env_extra={"WHISPER_DEVICE": "cpu"})
            fallback = "cpu"
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
        whisper_fallback=fallback,
    )
    print(f"[transcribe] done {duration:.1f}s words={word_count} fallback={fallback}")
    return {
        "transcript_path": str(transcript_path),
        "transcript_text": transcript_text,
        "transcript_word_count": word_count,
        "video_date": date,
    }
