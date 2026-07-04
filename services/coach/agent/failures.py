"""Fail-fast handling of SDK API-error replies (2026-07-04 outage).

When the bundled Claude CLI cannot reach the API it emits the literal text
"API Error: <detail>" as the assistant reply (observed: "API Error: Unable to
connect to API (ConnectionRefused)" after ~191s of internal retries). That text
must never be treated as a coaching draft: it would cascade into the grounding
gate + topic guard (two more doomed `claude -p` calls), the vault inbox, and
the user's chat.

Detection is a prefix match on the whole reply: a genuine coaching reply never
*starts* with "API Error", while every CLI connectivity/overload failure does.
"""
from __future__ import annotations

_API_ERROR_PREFIX = "api error"

# Honest, friendly retry messages for the two pilot languages. The agent knows
# only the Telegram language_code; anything non-Spanish gets English.
CONN_TROUBLE_REPLY = (
    "I'm having trouble reaching my thinking engine right now. "
    "I'm still here — send that again in a minute or two."
)
CONN_TROUBLE_REPLY_ES = (
    "Ahora mismo tengo problemas de conexión con mi motor de razonamiento. "
    "Sigo aquí — reenvíamelo en un minuto o dos."
)


def is_api_error_reply(text: str) -> bool:
    """True when the drafted reply IS a CLI API error, not coaching content."""
    return (text or "").strip().lower().startswith(_API_ERROR_PREFIX)


def conn_trouble_reply(language_code: str | None) -> str:
    """The user-facing message for a failed-fast turn, by client language."""
    if (language_code or "").strip().lower().startswith("es"):
        return CONN_TROUBLE_REPLY_ES
    return CONN_TROUBLE_REPLY
