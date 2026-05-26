"""HTTP client → coach agent service."""
from __future__ import annotations
import httpx


class CoachClient:
    def __init__(self, base_url: str, timeout: float = 60.0):
        self._client = httpx.Client(base_url=base_url, timeout=timeout)

    def turn(self, user_id: str, text: str) -> dict:
        r = self._client.post("/turn", json={"user_id": user_id, "text": text})
        r.raise_for_status()
        return r.json()
