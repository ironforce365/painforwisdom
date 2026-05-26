"""Maps Telegram user_id → claude-agent-sdk session_id (UUID).

Session_id is what we pass to `ClaudeAgentOptions(resume=session_id)` so per-user
multi-turn history persists across requests. Stored in-memory; on restart the
SDK can resume from JSONL on disk."""
from __future__ import annotations
import uuid
import threading


class SessionMap:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._map: dict[str, str] = {}

    def get_or_create_session_id(self, user_id: str) -> str:
        with self._lock:
            sid = self._map.get(user_id)
            if sid is None:
                sid = str(uuid.uuid4())
                self._map[user_id] = sid
            return sid

    def reset(self, user_id: str) -> None:
        with self._lock:
            self._map.pop(user_id, None)
