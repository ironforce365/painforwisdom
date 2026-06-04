"""Local STT via faster-whisper. Loads model lazily (1.5GB download on first use)."""
from __future__ import annotations
import functools
from pathlib import Path


@functools.lru_cache(maxsize=1)
def _model():
    from faster_whisper import WhisperModel
    return WhisperModel("base", compute_type="int8")


def transcribe_voice(audio_path: Path) -> str:
    segments, _info = _model().transcribe(str(audio_path))
    return " ".join(seg.text.strip() for seg in segments).strip()
