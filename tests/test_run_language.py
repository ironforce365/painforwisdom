"""The language chosen for a run: from the catalog or the launch form to Whisper and the YouTube fields."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from pipeline import publishing_config
from pipeline.nodes import transcribe


@pytest.fixture
def whisper(monkeypatch, tmp_path) -> list:
    """Stands in for extract_transcription.sh: records its argv and writes the transcript it would."""
    calls: list = []

    def run(argv, **_):
        calls.append(argv)
        out = tmp_path / "auto-generated"
        out.mkdir(exist_ok=True)
        (out / f"transcript_{argv[3]}.txt").write_text("word " * 40)
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(transcribe.subprocess, "run", run)
    return calls


def _state(tmp_path: Path, **extra) -> dict:
    return {"video_path": str(tmp_path / "PXL_20261030_070000000.mp4"), "run_dir": str(tmp_path), **extra}


def test_whisper_gets_the_language_chosen_for_the_run(whisper, tmp_path):  # [DT-S6]
    transcribe.node_transcribe(_state(tmp_path, language="es"))
    assert whisper[0][2] == "es"


def test_a_run_from_before_the_field_falls_back_to_the_catalog(whisper, tmp_path, monkeypatch):  # [DT-S6]
    path = tmp_path / "publishing.json"
    path.write_text(json.dumps({"language": "pt"}))
    monkeypatch.setattr(publishing_config, "CONFIG_PATH", path)
    transcribe.node_transcribe(_state(tmp_path))
    assert whisper[0][2] == "pt"


@pytest.mark.parametrize("profile", ["prod", "sandbox"])
def test_the_catalog_names_one_language_for_every_profile(profile):  # [DT-S6]
    assert publishing_config.load(profile)["language"] == "en"
    assert "language" not in publishing_config.load(profile)["youtube"]
