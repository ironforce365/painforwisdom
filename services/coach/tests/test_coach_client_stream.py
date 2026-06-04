"""CoachClient.stream_turn: opens a streaming POST to /turn/stream, parses
NDJSON, and yields each delta string in order until the done line.

The pre-existing blocking turn() method is exercised by test_coach_client.py and
must stay untouched.
"""
from __future__ import annotations
import json

import httpx
import respx

from telegram_bot.coach_client import CoachClient

BASE = "http://coach-agent:8800"


def _ndjson(*objs) -> bytes:
    return ("".join(json.dumps(o) + "\n" for o in objs)).encode("utf-8")


@respx.mock
def test_stream_turn_yields_deltas_in_order():
    body = _ndjson(
        {"delta": "Run "},
        {"delta": "in the "},
        {"delta": "rain."},
        {"done": True, "crisis": False},
    )
    respx.post(f"{BASE}/turn/stream").mock(
        return_value=httpx.Response(200, content=body)
    )

    cc = CoachClient(BASE)
    deltas = list(cc.stream_turn("1", "hi"))

    assert deltas == ["Run ", "in the ", "rain."]


@respx.mock
def test_stream_turn_stops_at_done():
    # Anything after the done line must not be yielded.
    body = _ndjson(
        {"delta": "hello"},
        {"done": True, "crisis": False},
        {"delta": "should-not-appear"},
    )
    respx.post(f"{BASE}/turn/stream").mock(
        return_value=httpx.Response(200, content=body)
    )

    cc = CoachClient(BASE)
    deltas = list(cc.stream_turn("1", "hi"))

    assert deltas == ["hello"]


@respx.mock
def test_stream_turn_sends_user_id_and_text():
    captured = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, content=_ndjson({"done": True, "crisis": False}))

    respx.post(f"{BASE}/turn/stream").mock(side_effect=_handler)

    cc = CoachClient(BASE)
    list(cc.stream_turn("42", "ping"))

    assert captured == {"user_id": "42", "text": "ping"}
