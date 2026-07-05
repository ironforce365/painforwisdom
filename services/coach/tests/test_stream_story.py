"""Streaming 'story' UX (2026-07-04 turn-latency review, R7).

The coach service streams the model's reasoning live as ``{"thinking": ...}``
chunks (so the user watches it think within seconds) and then the vetted answer
as ``{"delta": ...}`` — buffered + gated in prod so what reads as "the answer" is
never an un-gated draft. This pins that protocol plus the rationale sanitizer
(the thinking channel never passes the grounding gate, so it must not leak the
internal [[claim]] / <doctrine> scaffolding).
"""
from __future__ import annotations
import json

import pytest
from fastapi.testclient import TestClient

import agent.service as svc


def _parse(body: str) -> list[dict]:
    return [json.loads(line) for line in body.splitlines() if line.strip()]


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("COACH_INBOX_ROOT", str(tmp_path))
    monkeypatch.setenv("COACH_DEBUG", "false")
    # Keep side-work out of these UX tests.
    monkeypatch.setattr(svc, "write_user_memory", lambda *a, **k: None)
    monkeypatch.setattr(svc, "detect_validation_signals", lambda *a, **k: [])
    return TestClient(svc.app)


def test_thinking_streams_live_then_gated_answer(client, monkeypatch):
    """Gate ON (buffered): rationale streams live; the answer arrives once, gated."""
    monkeypatch.setenv("COACH_GROUNDING_GATE", "1")

    async def fake_stream(user_id, text):
        yield ("thinking", "Let me sit with ")
        yield ("thinking", "your week. ")
        yield "Draft answer."

    async def fake_gate(reply, **kw):
        return "GATED: " + reply

    monkeypatch.setattr(svc, "_stream_agent", fake_stream)
    monkeypatch.setattr(svc, "_gate_and_guard", fake_gate)

    with client.stream("POST", "/turn/stream", json={"user_id": "1", "text": "hi"}) as r:
        body = "".join(r.iter_text())

    lines = _parse(body)
    thinking = "".join(ln["thinking"] for ln in lines if "thinking" in ln)
    deltas = [ln["delta"] for ln in lines if "delta" in ln]
    assert thinking == "Let me sit with your week. "
    # Buffered: exactly one answer delta, and it's the GATED text (not the draft).
    assert deltas == ["GATED: Draft answer."]
    # thinking precedes the answer in the stream (the story order).
    first_delta_idx = next(i for i, ln in enumerate(lines) if "delta" in ln)
    last_think_idx = max(i for i, ln in enumerate(lines) if "thinking" in ln)
    assert last_think_idx < first_delta_idx
    assert lines[-1] == {"done": True, "crisis": False}


def test_thinking_strips_internal_scaffolding(client, monkeypatch):
    """The rationale is un-gated, so claim tags + protocol markers are stripped
    before it reaches the user — even when split across chunks."""
    monkeypatch.setenv("COACH_GROUNDING_GATE", "1")

    async def fake_stream(user_id, text):
        yield ("thinking", "I'll tag [[claim id=c1 ")
        yield ("thinking", "type=fact cite=M1]] the achilles, per <doctrine>x</doctrine>.")
        yield "Answer."

    async def fake_gate(reply, **kw):
        return reply

    monkeypatch.setattr(svc, "_stream_agent", fake_stream)
    monkeypatch.setattr(svc, "_gate_and_guard", fake_gate)

    with client.stream("POST", "/turn/stream", json={"user_id": "1", "text": "hi"}) as r:
        body = "".join(r.iter_text())

    thinking = "".join(ln["thinking"] for ln in _parse(body) if "thinking" in ln)
    assert "[[" not in thinking and "claim" not in thinking and "cite=M1" not in thinking
    assert "<doctrine>" not in thinking and "</doctrine>" not in thinking
    assert "the achilles" in thinking  # real reasoning survives


def test_rationale_kill_switch_suppresses_thinking(client, monkeypatch):
    """COACH_STREAM_RATIONALE=0 reverts to answer-only streaming."""
    monkeypatch.setenv("COACH_GROUNDING_GATE", "1")
    monkeypatch.setenv("COACH_STREAM_RATIONALE", "0")

    async def fake_stream(user_id, text):
        yield ("thinking", "hidden reasoning")
        yield "Answer."

    async def fake_gate(reply, **kw):
        return reply

    monkeypatch.setattr(svc, "_stream_agent", fake_stream)
    monkeypatch.setattr(svc, "_gate_and_guard", fake_gate)

    with client.stream("POST", "/turn/stream", json={"user_id": "1", "text": "hi"}) as r:
        body = "".join(r.iter_text())

    lines = _parse(body)
    assert not any("thinking" in ln for ln in lines)
    assert [ln["delta"] for ln in lines if "delta" in ln] == ["Answer."]


def test_gate_off_answer_still_streams_live(client, monkeypatch):
    """Gate OFF (legacy): the answer streams token-by-token; thinking still rides
    ahead of it when present."""
    monkeypatch.delenv("COACH_GROUNDING_GATE", raising=False)

    async def fake_stream(user_id, text):
        yield ("thinking", "quick thought ")
        for chunk in ["all ", "good"]:
            yield chunk

    monkeypatch.setattr(svc, "_stream_agent", fake_stream)

    with client.stream("POST", "/turn/stream", json={"user_id": "1", "text": "hi"}) as r:
        body = "".join(r.iter_text())

    lines = _parse(body)
    assert "".join(ln["thinking"] for ln in lines if "thinking" in ln) == "quick thought "
    # Unbuffered: each answer chunk is its own delta (live streaming).
    assert [ln["delta"] for ln in lines if "delta" in ln] == ["all ", "good"]


# --------------------------------------------------------------- sanitizer unit

def test_sanitizer_holds_tag_split_across_chunks():
    san = svc._RationaleSanitizer()
    a = san.feed("hello [[cla")
    b = san.feed("im id=c1 cite=M1]] world")
    tail = san.flush()
    joined = a + b + tail
    assert "[[" not in joined and "cite=M1" not in joined
    assert "hello" in joined and "world" in joined


def test_sanitizer_passes_clean_reasoning_through():
    san = svc._RationaleSanitizer()
    out = san.feed("The achilles quieting mid-run reads like adaptation.")
    assert out.strip() == "The achilles quieting mid-run reads like adaptation."
    assert san.flush() == ""
