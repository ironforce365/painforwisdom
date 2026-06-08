"""POST /turn/stream: NDJSON delta lines then a final done line.

Crisis input short-circuits to a single canned delta + done with crisis=true.
The streamer must not break the blocking /turn endpoint (covered separately).
"""
from __future__ import annotations
import json

import pytest
from fastapi.testclient import TestClient

import agent.service as svc


def _parse_ndjson(body: str) -> list[dict]:
    return [json.loads(line) for line in body.splitlines() if line.strip()]


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("COACH_INBOX_ROOT", str(tmp_path))
    # These tests pin the core NDJSON contract; the COACH_DEBUG canary footer
    # (default-on, exercised in test_debug_mode.py) would add an extra delta.
    monkeypatch.setenv("COACH_DEBUG", "false")
    return TestClient(svc.app)


def test_stream_yields_deltas_then_done(client, monkeypatch):
    async def fake_stream(user_id: str, text: str):
        for chunk in ["Run ", "in the ", "rain."]:
            yield chunk

    monkeypatch.setattr(svc, "_stream_agent", fake_stream)

    with client.stream("POST", "/turn/stream", json={"user_id": "1", "text": "hi"}) as r:
        assert r.status_code == 200
        body = "".join(r.iter_text())

    lines = _parse_ndjson(body)
    deltas = [ln["delta"] for ln in lines if "delta" in ln]
    assert "".join(deltas) == "Run in the rain."
    # Final line is the done marker.
    assert lines[-1] == {"done": True, "crisis": False}


def test_stream_crisis_short_circuits(client, monkeypatch):
    def _should_not_run(*a, **k):
        raise AssertionError("_stream_agent must not run for crisis input")

    monkeypatch.setattr(svc, "_stream_agent", _should_not_run)

    with client.stream("POST", "/turn/stream", json={"user_id": "1", "text": "I want to die"}) as r:
        assert r.status_code == 200
        body = "".join(r.iter_text())

    lines = _parse_ndjson(body)
    # Exactly one delta (the canned reply) then the done line with crisis=True.
    deltas = [ln for ln in lines if "delta" in ln]
    assert len(deltas) == 1
    assert "988" in deltas[0]["delta"]
    assert lines[-1] == {"done": True, "crisis": True}


def test_stream_writes_inbox_entry(client, monkeypatch, tmp_path):
    async def fake_stream(user_id: str, text: str):
        for chunk in ["hello ", "world"]:
            yield chunk

    written = {}

    def fake_write_inbox(*, inbox_root, user_id, user_text, assistant_text, retrieved_sources):
        written["assistant_text"] = assistant_text
        written["user_text"] = user_text
        written["user_id"] = user_id

    monkeypatch.setattr(svc, "_stream_agent", fake_stream)
    monkeypatch.setattr(svc, "write_inbox_entry", fake_write_inbox)

    with client.stream("POST", "/turn/stream", json={"user_id": "7", "text": "ping"}) as r:
        "".join(r.iter_text())

    assert written["assistant_text"] == "hello world"
    assert written["user_text"] == "ping"
    assert written["user_id"] == "7"
