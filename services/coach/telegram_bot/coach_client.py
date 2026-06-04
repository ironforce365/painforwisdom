"""HTTP client → coach agent service."""
from __future__ import annotations
import json
from typing import Iterator

import httpx

# A full coaching turn (vault RAG + memory search + multi-step agent loop) runs
# ~56s on a healthy system. The previous 60s flat timeout left only ~4s of
# margin, so normal variance crossed it and the bot reported a false "down"
# (incident 2026-06-03). Split the budget: a generous *read* deadline that
# clears worst-case turn latency, but a tight *connect* deadline so a genuinely
# unreachable agent still fails fast instead of hanging for the full read window.
DEFAULT_TIMEOUT = httpx.Timeout(connect=10.0, read=180.0, write=10.0, pool=10.0)

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

    def turn(self, user_id: str, text: str) -> dict:
        r = self._client.post("/turn", json={"user_id": user_id, "text": text})
        r.raise_for_status()
        return r.json()

    def stream_turn(self, user_id: str, text: str) -> Iterator[str]:
        """Stream a coaching turn from /turn/stream, yielding each assistant text
        delta as it arrives. Parses the NDJSON protocol — one JSON object per
        line, zero or more `{"delta": ...}` followed by a terminal
        `{"done": true, "crisis": ...}` — and stops at the done line.

        No single 56s request is held open from the bot's side beyond the read
        deadline because deltas flush incrementally; iteration ends at `done`.
        """
        with self._client.stream(
            "POST", "/turn/stream", json={"user_id": user_id, "text": text}
        ) as r:
            r.raise_for_status()
            for line in r.iter_lines():
                if not line.strip():
                    continue
                obj = json.loads(line)
                if obj.get("done"):
                    return
                if "delta" in obj:
                    yield obj["delta"]
