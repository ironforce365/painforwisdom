"""Telegram user allowlist. Schema borrowed from
anthropics/claude-plugins-official/external_plugins/telegram/access.json.

Backed by access.json on disk so the bot can mutate it at runtime (admin
approval flow) and survive restarts. Writes are atomic (.tmp + os.replace)."""
from __future__ import annotations
import json
import os
import threading
from pathlib import Path


class Allowlist:
    def __init__(self, access_json: Path):
        self._path = Path(access_json)
        self._lock = threading.Lock()
        self._reload()

    def _reload(self) -> None:
        data = json.loads(self._path.read_text(encoding="utf-8"))
        self._policy: str = data.get("policy", "allowlist")
        self._allowed: set[int] = set(data.get("allowed_user_ids", []))

    def allowed(self, telegram_user_id: int) -> bool:
        with self._lock:
            if self._policy != "allowlist":
                return False
            return telegram_user_id in self._allowed

    def add_user(self, telegram_user_id: int) -> bool:
        """Append a user_id and persist atomically. Returns True if newly added."""
        with self._lock:
            if self._policy != "allowlist":
                return False
            if telegram_user_id in self._allowed:
                return False
            self._allowed.add(telegram_user_id)
            data = {
                "version": 1,
                "policy": "allowlist",
                "allowed_user_ids": sorted(self._allowed),
            }
            tmp = self._path.with_suffix(self._path.suffix + ".tmp")
            tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
            os.replace(tmp, self._path)
            return True
