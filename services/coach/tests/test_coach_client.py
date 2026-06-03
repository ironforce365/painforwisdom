"""CoachClient timeout config + user-facing error mapping.

Regression coverage for the 2026-06-03 incident: a full coaching turn runs
~56s, but the bot's HTTP client had a 60s *read* timeout, so heavier-than-
average turns crossed 60s and the bot reported "Coach is down" even though the
agent was healthy and still working.
"""
from __future__ import annotations

import httpx

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
