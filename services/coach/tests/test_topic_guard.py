"""Coaching-topic guardrail (F4).

A second classifier that runs alongside the grounding judge: it checks the
coach's drafted reply is (a) about coaching — not off-topic chit-chat, code,
trivia — and (b) never offers a file/download/attachment. On a verdict of
off-topic or file-offer, the reply is replaced with a localized redirect so
the pilot can't be steered away from coaching or coaxed into sending files.

Default OFF (COACH_TOPIC_GUARD unset). LLM seam is ``classify_topic`` which
calls ``eval.llm.call_llm``; tests monkeypatch that, never the network."""
from __future__ import annotations

import json

import pytest

from eval.topic_guard import (
    TopicVerdict,
    classify_topic,
    guard_enabled,
    maybe_guard,
)


def _fake_llm(monkeypatch, payload: dict):
    """Make eval.topic_guard's call_llm return a canned JSON verdict."""
    import eval.topic_guard as tg

    monkeypatch.setattr(tg, "call_llm", lambda **kw: json.dumps(payload))


# ---- flag gating ---------------------------------------------------------

def test_guard_disabled_by_default(monkeypatch):
    monkeypatch.delenv("COACH_TOPIC_GUARD", raising=False)
    assert guard_enabled() is False


def test_guard_enabled_truthy(monkeypatch):
    monkeypatch.setenv("COACH_TOPIC_GUARD", "1")
    assert guard_enabled() is True


# ---- classifier parsing --------------------------------------------------

def test_classify_on_topic(monkeypatch):
    _fake_llm(monkeypatch, {"on_topic": True, "offers_file": False, "reason": "coaching"})
    v = classify_topic("Let's break your race goal into weekly steps.")
    assert isinstance(v, TopicVerdict)
    assert v.on_topic is True
    assert v.offers_file is False


def test_classify_off_topic(monkeypatch):
    _fake_llm(monkeypatch, {"on_topic": False, "offers_file": False, "reason": "python help"})
    v = classify_topic("Here's a Python script to sort a list...")
    assert v.on_topic is False


def test_classify_file_offer(monkeypatch):
    _fake_llm(monkeypatch, {"on_topic": True, "offers_file": True, "reason": "offered a PDF"})
    v = classify_topic("I'll send you a PDF of your plan — download it here.")
    assert v.offers_file is True


def test_classify_malformed_json_is_failsafe(monkeypatch):
    import eval.topic_guard as tg
    monkeypatch.setattr(tg, "call_llm", lambda **kw: "not json at all")
    # A garbled verdict must not block a turn: treat as clean (on-topic, no file).
    v = classify_topic("anything")
    assert v.on_topic is True
    assert v.offers_file is False


# ---- maybe_guard end to end ---------------------------------------------

def test_maybe_guard_off_returns_reply_untouched(monkeypatch):
    monkeypatch.delenv("COACH_TOPIC_GUARD", raising=False)
    reply = "Here's a Python script."
    assert maybe_guard(reply, language_code="en") == reply


def test_maybe_guard_passes_clean_reply(monkeypatch):
    monkeypatch.setenv("COACH_TOPIC_GUARD", "1")
    _fake_llm(monkeypatch, {"on_topic": True, "offers_file": False, "reason": "ok"})
    reply = "Let's set a goal for this week."
    assert maybe_guard(reply, language_code="en") == reply


def test_maybe_guard_redirects_off_topic(monkeypatch):
    monkeypatch.setenv("COACH_TOPIC_GUARD", "1")
    _fake_llm(monkeypatch, {"on_topic": False, "offers_file": False, "reason": "trivia"})
    out = maybe_guard("The capital of France is Paris.", language_code="en")
    assert "Paris" not in out
    assert "coach" in out.lower()


def test_maybe_guard_redirects_file_offer_spanish(monkeypatch):
    monkeypatch.setenv("COACH_TOPIC_GUARD", "1")
    _fake_llm(monkeypatch, {"on_topic": True, "offers_file": True, "reason": "pdf"})
    out = maybe_guard("Te envío un PDF con tu plan.", language_code="es")
    assert "PDF" not in out
    # Localized redirect (Spanish).
    assert "coach" in out.lower()


def test_maybe_guard_failsafe_on_llm_error(monkeypatch):
    monkeypatch.setenv("COACH_TOPIC_GUARD", "1")
    import eval.topic_guard as tg

    def _boom(**kw):
        raise RuntimeError("cli down")

    monkeypatch.setattr(tg, "call_llm", _boom)
    reply = "Let's keep working on your goal."
    # Guard failure must never break a turn → original reply passes through.
    assert maybe_guard(reply, language_code="en") == reply
