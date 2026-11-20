"""Readiness warnings gate publish (io-8fc1eff4): the refusal of run 2026-11-19_170856_ui went live unwarned."""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from pipeline import publishing_config
from pipeline.ui import launcher, publish
from pipeline.ui.app import create_app
from pipeline.ui.settings import Settings

RUN = "2026-11-19_170856_ui"
FIX = Path(__file__).parent / "fixtures"


@pytest.fixture
def processed(tmp_path, monkeypatch) -> Path:
    shutil.copytree(FIX / "runs" / RUN, tmp_path / "processed" / RUN)
    monkeypatch.setattr(launcher, "spawn", lambda argv, log: os.getpid())
    monkeypatch.setattr(publishing_config, "CONFIG_PATH", FIX / "ui" / "publishing.json")
    return tmp_path / "processed"


def _client(processed: Path) -> TestClient:
    return TestClient(create_app(Settings(processed_root=processed, inbox=FIX / "ui" / "inbox")), base_url="http://127.0.0.1")


def test_weak_run_refuses_publish_until_acknowledged(processed):
    client = _client(processed)
    warnings = client.get(f"/api/runs/{RUN}/publish").json()["warnings"]
    assert warnings == ["The extraction rated the content Weak."]
    fields = client.post(f"/api/runs/{RUN}/publish/blog/prepare").json()["fields"]

    r = client.post(f"/api/runs/{RUN}/publish/blog", json={"fields": fields})
    assert r.status_code == 409
    assert r.json() == {"detail": "unacknowledged warnings", "warnings": warnings}
    assert json.loads((processed / RUN / "langgraph" / "blog-publish" / "package.json").read_text())["status"] == "pending"

    r = client.post(f"/api/runs/{RUN}/publish/blog", json={"fields": fields, "acknowledge_warnings": True})
    assert r.status_code == 202, r.text
    assert r.json()["status"] == "publishing"


def test_declined_writer_and_missing_verdict_are_warnings(processed):
    rd = processed / RUN / "langgraph"
    (rd / "painforwisdom-writer" / "DECLINED.md").write_text("WRITER_DECLINED: no story\n")
    rec = json.loads((rd / "run.json").read_text())
    (rd / "run.json").write_text(json.dumps({**rec, "verdict": None}))
    assert publish.readiness(rd) == [
        "The writer declined to write this post (painforwisdom-writer/DECLINED.md).",
        "The extraction rated the content Weak.",
        "The validator verdict is missing, not PASS.",
    ]


def test_strong_passing_run_has_no_warnings(processed):
    rd = processed / RUN / "langgraph"
    report = rd / "coaching-thought-extractor" / "extraction_report.md"
    report.write_text(report.read_text().replace("**Content Quality:** Weak", "**Content Quality:** Strong"))
    assert publish.readiness(rd) == []
