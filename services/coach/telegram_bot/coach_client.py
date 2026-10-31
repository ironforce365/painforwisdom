"""HTTP client → coach agent service."""
from __future__ import annotations
import json
import time
from typing import Iterator

import httpx

from telegram_bot import metrics

# A full coaching turn (vault RAG + memory search + multi-step agent loop) runs
# ~56s on a healthy system. The previous 60s flat timeout left only ~4s of
# margin, so normal variance crossed it and the bot reported a false "down"
# (incident 2026-06-03). Split the budget: a generous *read* deadline that
# clears worst-case turn latency, but a tight *connect* deadline so a genuinely
# unreachable agent still fails fast instead of hanging for the full read window.
# Read = 240s: the agent now enforces its OWN budgets (COACH_STREAM_BUDGET_S=150
# generation + ≤60s gate/guard, 2026-07-04 outage) and answers honestly within
# ~215s worst-case, so this deadline is the last resort — every degraded turn
# used to cross the old 180s and read as "Still with you".
DEFAULT_TIMEOUT = httpx.Timeout(connect=10.0, read=240.0, write=10.0, pool=10.0)

# User-facing replies for the two failure classes the bot must tell apart.
TIMEOUT_REPLY = "Still with you — this one's taking longer than usual. Give me a moment and resend if you don't hear back."
DOWN_REPLY = "Coach is down. Try again in a minute."


def coach_error_reply(exc: Exception) -> str:
    """Map a turn() failure to the message the user should see.

    A ReadTimeout means the agent received the request and is still working —
    not an outage. ConnectTimeout/ConnectError (and anything else) mean the
    agent was unreachable or genuinely broken → the real "down" message.
    """
    if isinstance(exc, (httpx.ReadTimeout, httpx.WriteTimeout, httpx.PoolTimeout)):
        return TIMEOUT_REPLY
    return DOWN_REPLY


class CoachClient:
    def __init__(self, base_url: str, timeout: httpx.Timeout | float | None = None):
        self.timeout = DEFAULT_TIMEOUT if timeout is None else timeout
        self._client = httpx.Client(base_url=base_url, timeout=self.timeout)

    def turn(self, user_id: str, text: str, language_code: str | None = None,
             channel: str = "live") -> dict:
        # Time the turn for p95 monitoring. Classify the exit: "ok" on success,
        # "timeout" for httpx read/write/pool timeouts (agent alive but slow),
        # "down" for anything else (unreachable/broken), then re-raise so caller
        # behaviour is unchanged. channel="test" (synthetic harness) skips the inbox.
        start = time.monotonic()
        try:
            r = self._client.post(
                "/turn",
                json={"user_id": user_id, "text": text,
                      "language_code": language_code, "channel": channel},
            )
            r.raise_for_status()
            result = r.json()
        except (httpx.ReadTimeout, httpx.WriteTimeout, httpx.PoolTimeout):
            metrics.record(time.monotonic() - start, "timeout")
            raise
        except Exception:
            metrics.record(time.monotonic() - start, "down")
            raise
        metrics.record(time.monotonic() - start, "ok")
        return result

    def reset(self, user_id: str) -> dict:
        """Ask the agent to start this user over: drop their session + mem0
        facts (the bot's /restart command). Short timeout — it's a cheap call."""
        r = self._client.post("/reset", json={"user_id": user_id})
        r.raise_for_status()
        return r.json()

    def outreach(
        self,
        user_id: str,
        *,
        kind: str = "inactivity",
        language_code: str | None = None,
        last_coach_text: str | None = None,
    ) -> dict:
        """Ask the agent to compose a proactive re-engagement message for a quiet
        user (the scheduler's outreach job). Coach-initiated — the agent writes no
        inbox entry and consumes no quota."""
        r = self._client.post(
            "/outreach",
            json={
                "user_id": user_id,
                "kind": kind,
                "language_code": language_code,
                "last_coach_text": last_coach_text,
            },
        )
        r.raise_for_status()
        return r.json()

    def stream_turn(
        self, user_id: str, text: str, language_code: str | None = None
    ) -> Iterator:
        """Stream a coaching turn from /turn/stream. Parses the NDJSON protocol —
        one JSON object per line — and yields, in order, until the terminal
        `{"done": true, ...}` line:

        - `("thinking", <text>)` for a live rationale chunk (`{"thinking": ...}`)
        - a bare `<str>` for an answer chunk (`{"delta": ...}`)

        Bare-str answers keep the pre-existing contract (and every test stub that
        yields plain strings) valid; the thinking tuples are additive.

        No single ~56s request is held open beyond the read deadline because chunks
        flush incrementally; iteration ends at `done`.

        Instrumented for p95 monitoring the same way as turn(): the clock spans
        the whole stream, recording "ok" once the stream completes, "timeout" on
        httpx read/write/pool timeouts, "down" on anything else, then re-raising.
        """
        start = time.monotonic()
        try:
            with self._client.stream(
                "POST",
                "/turn/stream",
                json={"user_id": user_id, "text": text, "language_code": language_code},
            ) as r:
                r.raise_for_status()
                for line in r.iter_lines():
                    if not line.strip():
                        continue
                    obj = json.loads(line)
                    if obj.get("done"):
                        break
                    if "thinking" in obj:
                        yield ("thinking", obj["thinking"])
                    elif "delta" in obj:
                        yield obj["delta"]
        except (httpx.ReadTimeout, httpx.WriteTimeout, httpx.PoolTimeout):
            metrics.record(time.monotonic() - start, "timeout")
            raise
        except Exception:
            metrics.record(time.monotonic() - start, "down")
            raise
        metrics.record(time.monotonic() - start, "ok")
