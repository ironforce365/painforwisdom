"""Telegram user allowlist. Schema borrowed from
anthropics/claude-plugins-official/external_plugins/telegram/access.json.

Backed by access.json on disk so the bot can mutate it at runtime (admin
approval flow) and survive restarts.

Writes overwrite the file IN PLACE (truncate + write the same inode), NOT via
the usual atomic tmp+os.replace. access.json is bind-mounted into the container
as a SINGLE FILE, and a rename cannot cross that mountpoint — os.replace would
either fail or write to a shadow file that never reaches the host, so runtime
approvals silently vanished on the next restart. The file is tiny and writes are
lock-serialized; a crash mid-write is the only loss window and is acceptable for
a 3-user allowlist."""
from __future__ import annotations
import json
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
            # In-place overwrite (see module docstring): the path may be a
            # single-file bind mount, so we must not rename a temp file over it.
            self._path.write_text(json.dumps(data, indent=2), encoding="utf-8")
            return True
