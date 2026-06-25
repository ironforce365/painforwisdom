"""CoachClient timeout config + user-facing error mapping.

Regression coverage for the 2026-06-03 incident: a full coaching turn runs
~56s, but the bot's HTTP client had a 60s *read* timeout, so heavier-than-
average turns crossed 60s and the bot reported "Coach is down" even though the
agent was healthy and still working.
"""
from __future__ import annotations

import httpx
import pytest
import respx
from httpx import Response

from telegram_bot import metrics
from telegram_bot.coach_client import (
    DOWN_REPLY,
    TIMEOUT_REPLY,
    CoachClient,
    coach_error_reply,
)


def test_default_read_timeout_clears_worst_case_turn():
    # Worst-case healthy turn measured ~56s; the read budget must comfortably
    # exceed that so normal variance never trips a false "down".
    cc = CoachClient("http://coach-agent:8800")
    assert cc.timeout.read >= 180.0


def test_default_connect_timeout_stays_fast():
    # A genuinely dead/unreachable agent must still fail fast, not hang for the
    # full read budget — keep connect tight.
    cc = CoachClient("http://coach-agent:8800")
    assert cc.timeout.connect <= 15.0


def test_read_timeout_reads_as_still_thinking():
    # Agent got the request and is slow → not an outage.
    assert coach_error_reply(httpx.ReadTimeout("timed out")) == TIMEOUT_REPLY
    assert coach_error_reply(httpx.ReadTimeout("timed out")) != DOWN_REPLY


def test_connect_failures_read_as_down():
    # Could not even reach the agent → real outage, keep the "down" message.
    assert coach_error_reply(httpx.ConnectTimeout("no route")) == DOWN_REPLY
    assert coach_error_reply(httpx.ConnectError("refused")) == DOWN_REPLY


def test_unknown_error_reads_as_down():
    assert coach_error_reply(RuntimeError("boom")) == DOWN_REPLY


@pytest.fixture
def fresh_recorder(monkeypatch):
    """Reset the metrics singleton so each turn() test sees its own samples."""
    monkeypatch.setattr(metrics.recorder, "_latencies", [])
    monkeypatch.setattr(
        metrics.recorder, "_outcomes", {"ok": 0, "timeout": 0, "down": 0}
    )
    return metrics.recorder


@respx.mock
def test_turn_records_ok_on_success(fresh_recorder):
    respx.post("http://coach-agent:8800/turn").mock(
        return_value=Response(200, json={"reply": "hi"})
    )
    cc = CoachClient("http://coach-agent:8800")
    result = cc.turn(user_id="42", text="hello")
    assert result == {"reply": "hi"}  # return value unchanged
    snap = fresh_recorder.snapshot()
    assert snap["count"] == 1
    assert snap["outcomes"]["ok"] == 1


@respx.mock
def test_turn_records_timeout_and_reraises(fresh_recorder):
    respx.post("http://coach-agent:8800/turn").mock(
        side_effect=httpx.ReadTimeout("timed out")
    )
    cc = CoachClient("http://coach-agent:8800")
    with pytest.raises(httpx.ReadTimeout):
        cc.turn(user_id="42", text="hello")
    snap = fresh_recorder.snapshot()
    assert snap["outcomes"]["timeout"] == 1
    assert snap["outcomes"]["ok"] == 0


@respx.mock
def test_turn_records_down_on_other_error_and_reraises(fresh_recorder):
    respx.post("http://coach-agent:8800/turn").mock(
        side_effect=httpx.ConnectError("refused")
    )
    cc = CoachClient("http://coach-agent:8800")
    with pytest.raises(httpx.ConnectError):
        cc.turn(user_id="42", text="hello")
    snap = fresh_recorder.snapshot()
    assert snap["outcomes"]["down"] == 1


@respx.mock
def test_turn_forwards_test_channel(fresh_recorder):
    # The synthetic harness sends channel="test" so the agent skips the inbox.
    route = respx.post("http://coach-agent:8800/turn").mock(
        return_value=Response(200, json={"reply": "hi"})
    )
    cc = CoachClient("http://coach-agent:8800")
    cc.turn(user_id="synthetic-x", text="hello", channel="test")
    assert b'"channel":"test"' in route.calls.last.request.content


@respx.mock
def test_turn_defaults_to_live_channel(fresh_recorder):
    route = respx.post("http://coach-agent:8800/turn").mock(
        return_value=Response(200, json={"reply": "hi"})
    )
    CoachClient("http://coach-agent:8800").turn(user_id="1", text="hello")
    assert b'"channel":"live"' in route.calls.last.request.content


@respx.mock
def test_outreach_posts_kind_and_returns_message():
    # Proactive outreach: bot asks the agent to compose a re-engagement message.
    route = respx.post("http://coach-agent:8800/outreach").mock(
        return_value=Response(200, json={"reply": "still here when you are", "crisis": False})
    )
    cc = CoachClient("http://coach-agent:8800")
    result = cc.outreach("42", kind="followup", language_code="en",
                         last_coach_text="report back after your run")
    assert result["reply"] == "still here when you are"
    assert route.called
    body = route.calls.last.request.content
    assert b'"kind":"followup"' in body
    assert b'"user_id":"42"' in body
    assert b"report back after your run" in body


@respx.mock
def test_reset_posts_user_id_to_reset_endpoint():
    # /restart → bot asks the agent to drop this user's session + mem0.
    route = respx.post("http://coach-agent:8800/reset").mock(
        return_value=Response(200, json={"status": "reset", "user_id": "42"})
    )
    cc = CoachClient("http://coach-agent:8800")
    result = cc.reset("42")
    assert result["status"] == "reset"
    assert route.called
    assert b'"user_id":"42"' in route.calls.last.request.content
