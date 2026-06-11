"""Validation-signal detector: match a user reply against the coach's open questions.

The user never references claim IDs — they just answer in their own words (parent
spec §276: semantic match, not exact reference). One batched subscription LLM call
classifies each open item as confirmed / corrected / unaddressed and extracts the
correction in the user's words when present.

Defensive by contract: malformed LLM output degrades to all-unaddressed (items
stay open for the next turn); it must never break a live turn. ``llm_fn`` is the
monkeypatch seam (defaults to the subscription ``claude -p`` seam).
"""
from __future__ import annotations

import json
from typing import Callable

from eval.llm import call_llm

OUTCOMES = {"confirmed", "corrected", "unaddressed"}

_SYSTEM = (
    "You are a validation-signal detector for a coaching dialogue. The coach "
    "previously made tentative reads / asked questions (OPEN ITEMS below). The "
    "user has now replied. For EACH open item, decide from the user's reply:\n"
    "- confirmed: the reply affirms the read (explicitly or clearly in substance)\n"
    "- corrected: the reply contradicts or amends the read; quote the user's "
    "correction as correction_text, staying close to their words\n"
    "- unaddressed: the reply doesn't speak to this item\n"
    "The user never references items by id or verbatim — match on meaning. Be "
    "conservative: when in doubt, unaddressed. A reply can address several items.\n"
    'Return ONLY JSON: {"outcomes":[{"claim_id","outcome","correction_text"}]} '
    "with one entry per open item, in order."
)


def _fmt_items(items: list[dict]) -> str:
    lines = []
    for i in items:
        asked = i.get("question") or i.get("claim_text", "")
        lines.append(
            f'{i["claim_id"]} | action={i.get("action", "?")} | '
            f'read="{i.get("claim_text", "")}" | asked="{asked}"'
        )
    return "\n".join(lines)


def detect(
    user_text: str,
    open_items: list[dict],
    *,
    llm_fn: Callable = call_llm,
    model: str = "claude-sonnet-4-6",
) -> list[dict]:
    """Classify a user reply against open validations.

    Returns one ``{claim_id, outcome, correction_text}`` per open item (order
    preserved). Empty open set → no LLM call, empty result.
    """
    if not open_items:
        return []
    fallback = [
        {"claim_id": i["claim_id"], "outcome": "unaddressed", "correction_text": ""}
        for i in open_items
    ]
    try:
        raw = llm_fn(
            system=_SYSTEM,
            user=f"OPEN ITEMS:\n{_fmt_items(open_items)}\n\nUSER REPLY:\n{user_text}",
            model=model,
        )
        start, end = raw.find("{"), raw.rfind("}")
        if start == -1 or end < start:
            return fallback
        data = json.loads(raw[start : end + 1])
    except Exception:  # noqa: BLE001 - detector must never break a turn
        return fallback

    known = {i["claim_id"] for i in open_items}
    by_id: dict[str, dict] = {}
    for o in data.get("outcomes", []):
        cid = o.get("claim_id")
        if cid not in known:
            continue  # hallucinated id — drop
        outcome = o.get("outcome", "unaddressed")
        if outcome not in OUTCOMES:
            outcome = "unaddressed"
        by_id[cid] = {
            "claim_id": cid,
            "outcome": outcome,
            "correction_text": str(o.get("correction_text") or ""),
        }
    # every open item gets a row; LLM omissions stay unaddressed (= stay open)
    return [
        by_id.get(i["claim_id"],
                  {"claim_id": i["claim_id"], "outcome": "unaddressed", "correction_text": ""})
        for i in open_items
    ]
