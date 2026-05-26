"""Thin HTTP client around the Mem0 OSS REST API."""
from __future__ import annotations
import httpx


class Mem0Client:
    def __init__(self, base_url: str, timeout: float = 10.0):
        self._base = base_url.rstrip("/")
        self._client = httpx.Client(timeout=timeout)

    def add(self, user_id: str, text: str) -> dict:
        r = self._client.post(
            f"{self._base}/memories",
            json={"user_id": user_id, "messages": [{"role": "user", "content": text}]},
        )
        r.raise_for_status()
        return r.json()

    def search(self, user_id: str, query: str, limit: int = 5) -> list[dict]:
        r = self._client.post(
            f"{self._base}/memories/search",
            json={"user_id": user_id, "query": query, "limit": limit},
        )
        r.raise_for_status()
        return r.json().get("results", [])

    def close(self) -> None:
        self._client.close()
