"""Cross-MCP-server validation helpers."""
from __future__ import annotations


def validate_telegram_user_id(user_id: str) -> None:
    """Reject any user_id that isn't a numeric Telegram ID (1..19 digits).

    Telegram numeric IDs fit in int64 (max 19 digits). Rejecting non-digits at
    the MCP tool boundary prevents the agent from passing typos or attacker-
    influenced strings through to the storage layer.
    """
    if not user_id.isdigit() or not (1 <= len(user_id) <= 19):
        raise ValueError(f"invalid user_id: {user_id!r}")
