"""Voice transcription returns a string (content unverified — the fixture is a tone)."""
from __future__ import annotations
from pathlib import Path
import pytest

from telegram_bot.voice import transcribe_voice

FIX = Path(__file__).parent / "fixtures" / "audio" / "hello.ogg"


@pytest.mark.skipif(not FIX.exists(), reason="audio fixture missing; run ffmpeg step")
def test_transcribe_returns_string():
    text = transcribe_voice(FIX)
    assert isinstance(text, str)
