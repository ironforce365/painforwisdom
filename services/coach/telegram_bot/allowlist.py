"""Telegram user allowlist. Schema borrowed from
anthropics/claude-plugins-official/external_plugins/telegram/access.json."""
from __future__ import annotations
import json
from pathlib import Path


class Allowlist:
    def __init__(self, access_json: Path):
        data = json.loads(Path(access_json).read_text(encoding="utf-8"))
        self._policy: str = data.get("policy", "allowlist")
        self._allowed: set[int] = set(data.get("allowed_user_ids", []))

    def allowed(self, telegram_user_id: int) -> bool:
        if self._policy != "allowlist":
            return False
        return telegram_user_id in self._allowed
