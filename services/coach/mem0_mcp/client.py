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
        # mem0 OSS server: endpoint is POST /search; per-user scoping goes in
        # `filters` (top-level user_id is deprecated), result count is `top_k`.
        r = self._client.post(
            f"{self._base}/search",
            json={"query": query, "filters": {"user_id": user_id}, "top_k": limit},
        )
        r.raise_for_status()
        data = r.json()
        # v1.1 returns {"results": [...]}; tolerate a bare list for safety.
        return data.get("results", []) if isinstance(data, dict) else data

    def delete(self, user_id: str) -> dict:
        """Delete ALL memories for a user (used by the coach's /restart)."""
        r = self._client.delete(f"{self._base}/memories/{user_id}")
        r.raise_for_status()
        return r.json()

    def close(self) -> None:
        self._client.close()
