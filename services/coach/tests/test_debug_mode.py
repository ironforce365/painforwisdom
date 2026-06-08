"""COACH_DEBUG canary: when enabled, the coach reply carries a footer listing the
vault source slugs that grounded the turn (or '(none)' — the generic-answer
alarm). Toggle via the COACH_DEBUG env var; default-on for now.

The footer is a presentation concern: it is appended to what the client sees but
NOT written to the inbox (inbox stays clean) and NOT shown on the crisis path.
"""
from __future__ import annotations
import json

import pytest
from fastapi.testclient import TestClient

import agent.service as svc


# --- helper: _debug_enabled -------------------------------------------------

@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on", "On"])
def test_debug_enabled_truthy_values(monkeypatch, value):
    monkeypatch.setenv("COACH_DEBUG", value)
    assert svc._debug_enabled() is True


@pytest.mark.parametrize("value", ["0", "false", "FALSE", "no", "off", ""])
def test_debug_enabled_falsy_values(monkeypatch, value):
    monkeypatch.setenv("COACH_DEBUG", value)
    assert svc._debug_enabled() is False


def test_debug_enabled_defaults_on_when_unset(monkeypatch):
    """Default-on for now: unset COACH_DEBUG behaves as enabled."""
    monkeypatch.delenv("COACH_DEBUG", raising=False)
    assert svc._debug_enabled() is True


# --- helper: _debug_sources_footer ------------------------------------------

def test_footer_empty_when_debug_disabled(monkeypatch):
    monkeypatch.setenv("COACH_DEBUG", "false")
    assert svc._debug_sources_footer(["comfort-as-default", "phase-1-protocol"]) == ""


def test_footer_lists_slugs_when_enabled(monkeypatch):
    monkeypatch.setenv("COACH_DEBUG", "true")
    footer = svc._debug_sources_footer(["comfort-as-default", "phase-1-protocol"])
    assert "comfort-as-default" in footer
    assert "phase-1-protocol" in footer
    # Distinct, recognizable label so the canary is unmistakable in the chat.
    assert "kb sources" in footer.lower()


def test_footer_reports_none_as_canary_when_no_slugs(monkeypatch):
    """The whole point: debug on + zero retrieved slugs == generic-answer alarm."""
    monkeypatch.setenv("COACH_DEBUG", "true")
    footer = svc._debug_sources_footer([])
    assert "kb sources" in footer.lower()
    assert "none" in footer.lower()


# --- /turn endpoint ---------------------------------------------------------

def _turn_client(monkeypatch, tmp_path, sources):
    monkeypatch.setenv("COACH_INBOX_ROOT", str(tmp_path))

    async def fake_chat(user_id: str, text: str):
        return ("ack: " + text, list(sources))

    monkeypatch.setattr(svc, "_chat_with_agent", fake_chat)
    return TestClient(svc.app)


def test_turn_appends_footer_when_debug_on(monkeypatch, tmp_path):
    monkeypatch.setenv("COACH_DEBUG", "true")
    client = _turn_client(monkeypatch, tmp_path, ["comfort-as-default"])
    reply = client.post("/turn", json={"user_id": "1", "text": "hi"}).json()["reply"]
    assert reply.startswith("ack: hi")
    assert "kb sources" in reply.lower()
    assert "comfort-as-default" in reply


def test_turn_no_footer_when_debug_off(monkeypatch, tmp_path):
    monkeypatch.setenv("COACH_DEBUG", "false")
    client = _turn_client(monkeypatch, tmp_path, ["comfort-as-default"])
    reply = client.post("/turn", json={"user_id": "1", "text": "hi"}).json()["reply"]
    assert reply == "ack: hi"


def test_turn_inbox_excludes_footer(monkeypatch, tmp_path):
    """The footer is presentation-only — the persisted inbox reply stays clean."""
    monkeypatch.setenv("COACH_DEBUG", "true")
    monkeypatch.setenv("COACH_INBOX_ROOT", str(tmp_path))

    async def fake_chat(user_id: str, text: str):
        return ("ack: " + text, ["comfort-as-default"])

    captured = {}

    def fake_write_inbox(*, inbox_root, user_id, user_text, assistant_text, retrieved_sources):
        captured["assistant_text"] = assistant_text

    monkeypatch.setattr(svc, "_chat_with_agent", fake_chat)
    monkeypatch.setattr(svc, "write_inbox_entry", fake_write_inbox)

    TestClient(svc.app).post("/turn", json={"user_id": "1", "text": "hi"})
    assert captured["assistant_text"] == "ack: hi"
    assert "kb sources" not in captured["assistant_text"].lower()


# --- /turn/stream endpoint --------------------------------------------------

def _parse_ndjson(body: str) -> list[dict]:
    return [json.loads(line) for line in body.splitlines() if line.strip()]


def _stream_body(client, text):
    with client.stream("POST", "/turn/stream", json={"user_id": "1", "text": text}) as r:
        return "".join(r.iter_text())


def test_stream_emits_footer_delta_when_debug_on(monkeypatch, tmp_path):
    monkeypatch.setenv("COACH_INBOX_ROOT", str(tmp_path))
    monkeypatch.setenv("COACH_DEBUG", "true")

    async def fake_stream(user_id: str, text: str):
        # Mimic the real streamer seeding the per-stream source sink.
        svc._stream_sources.get().append("comfort-as-default")
        for chunk in ["Run ", "in the rain."]:
            yield chunk

    monkeypatch.setattr(svc, "_stream_agent", fake_stream)
    lines = _parse_ndjson(_stream_body(TestClient(svc.app), "hi"))

    deltas = [ln["delta"] for ln in lines if "delta" in ln]
    joined = "".join(deltas)
    assert "Run in the rain." in joined
    assert "kb sources" in joined.lower()
    assert "comfort-as-default" in joined
    # Footer rides as a delta BEFORE the done marker, which stays last.
    assert lines[-1] == {"done": True, "crisis": False}


def test_stream_no_footer_delta_when_debug_off(monkeypatch, tmp_path):
    monkeypatch.setenv("COACH_INBOX_ROOT", str(tmp_path))
    monkeypatch.setenv("COACH_DEBUG", "false")

    async def fake_stream(user_id: str, text: str):
        svc._stream_sources.get().append("comfort-as-default")
        for chunk in ["Run ", "in the rain."]:
            yield chunk

    monkeypatch.setattr(svc, "_stream_agent", fake_stream)
    lines = _parse_ndjson(_stream_body(TestClient(svc.app), "hi"))

    joined = "".join(ln["delta"] for ln in lines if "delta" in ln)
    assert joined == "Run in the rain."
    assert "kb sources" not in joined.lower()


def test_stream_crisis_emits_no_footer(monkeypatch, tmp_path):
    """Crisis short-circuits before retrieval — no grounding, so no canary."""
    monkeypatch.setenv("COACH_INBOX_ROOT", str(tmp_path))
    monkeypatch.setenv("COACH_DEBUG", "true")

    def _should_not_run(*a, **k):
        raise AssertionError("_stream_agent must not run for crisis input")

    monkeypatch.setattr(svc, "_stream_agent", _should_not_run)
    lines = _parse_ndjson(_stream_body(TestClient(svc.app), "I want to die"))

    joined = "".join(ln["delta"] for ln in lines if "delta" in ln)
    assert "kb sources" not in joined.lower()
    assert lines[-1] == {"done": True, "crisis": True}
