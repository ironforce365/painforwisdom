"""Conversation-only per-user memory for the coach (Stream 3).

The coach must know facts about the person it's talking to ONLY from what that
person actually said — never from the vault. mem0 (per-user, already deployed) is
that store: read before the turn (→ an `<about_this_user>` block + an `M1`
grounding source), written after the turn from the user's OWN words.

Every function degrades to a no-op / blank on failure: memory must never break a
live turn. Nothing here ever touches the vault — separation is structural.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

log = logging.getLogger("coach.memory")

_client = None  # lazy process-wide singleton


def _default_client():
    global _client
    if _client is None:
        from mem0_mcp.client import Mem0Client

        _client = Mem0Client(base_url=os.environ.get("MEM0_API_URL", "http://mem0-api:8000"))
    return _client


def set_client_for_tests(client) -> None:
    global _client
    _client = client


def _memory_text(item) -> str:
    """Extract the memory string from a mem0 result item (dict or bare str)."""
    if isinstance(item, str):
        return item.strip()
    if isinstance(item, dict):
        return str(item.get("memory") or item.get("text") or "").strip()
    return ""


def format_about_user(memories: list[str]) -> str:
    """Render recalled memories as the `<about_this_user>` block, or '' if none."""
    lines = [m for m in (s.strip() for s in memories) if m]
    if not lines:
        return ""
    body = "\n".join(f"- {m}" for m in lines)
    return (
        "<about_this_user>\n"
        "What you remember about this person from past conversations with THEM "
        "(their own words). These are the only facts you know about this person; "
        "do not invent beyond them or this conversation.\n\n"
        f"{body}\n"
        "</about_this_user>"
    )


def read_user_memory(
    user_id: str, query: str, *, client=None, limit: int = 5
) -> tuple[str, str]:
    """Recall memories for this user relevant to the turn.

    Returns ``(block, source_text)``: the XML block for the prompt, and the raw
    memories joined (no XML) for the grounding judge's M1 source. ``("", "")`` on
    empty results or any failure.
    """
    client = client or _default_client()
    try:
        results = client.search(user_id, query, limit)
    except Exception:
        log.exception("mem0 search failed; proceeding without user memory")
        return "", ""
    memories = [t for t in (_memory_text(r) for r in (results or [])) if t]
    if not memories:
        return "", ""
    return format_about_user(memories), "\n".join(memories)


def write_user_memory(user_id: str, user_text: str, *, client=None) -> None:
    """Persist the user's OWN words to mem0 (it extracts durable facts).

    Conversation-only by construction: only the user's message is passed — never
    the coach's reply (its inferences must not become 'memory') and never the
    vault. Swallows all failures.
    """
    text = (user_text or "").strip()
    if not text:
        return
    client = client or _default_client()
    try:
        client.add(user_id, text)
    except Exception:
        log.exception("mem0 add failed; turn memory not persisted")


def delete_user_memory(user_id: str, *, client=None) -> None:
    """Delete ALL stored memories for a user (the coach's /restart).

    Best-effort like the rest of this module: a mem0 outage must never break the
    /restart flow, so failures are swallowed."""
    client = client or _default_client()
    try:
        client.delete(user_id)
    except Exception:
        log.exception("mem0 delete failed; user memory not cleared")
