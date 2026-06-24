"""Persistent registry of users who have already received the welcome message.

Backed by a JSON file on disk (atomic .tmp + os.replace, like Allowlist) so the
welcome fires exactly once per user and survives bot restarts / redeploys. Unlike
the allowlist the file is optional: a missing file means 'nobody welcomed yet'
and is created on the first mark()."""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path


class WelcomeRegistry:
    def __init__(self, path: Path):
        self._path = Path(path)
        self._lock = threading.Lock()
        self._welcomed: set[int] = set()
        if self._path.exists():
            data = json.loads(self._path.read_text(encoding="utf-8"))
            self._welcomed = set(data.get("welcomed_user_ids", []))

    def seen(self, telegram_user_id: int) -> bool:
        with self._lock:
            return telegram_user_id in self._welcomed

    def mark(self, telegram_user_id: int) -> bool:
        """Record that a user has been welcomed. Returns True if newly added."""
        with self._lock:
            if telegram_user_id in self._welcomed:
                return False
            self._welcomed.add(telegram_user_id)
            self._path.parent.mkdir(parents=True, exist_ok=True)
            data = {"version": 1, "welcomed_user_ids": sorted(self._welcomed)}
            tmp = self._path.with_suffix(self._path.suffix + ".tmp")
            tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
            os.replace(tmp, self._path)
            return True
