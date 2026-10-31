"""Turn-prompt composition + source merging for deterministic pre-retrieval.

These wire the in-process pre-retrieval (agent/retrieval.py) into the agent
query so every turn is grounded and the inbox records the vault sources even
when the model adds no search_vault calls of its own.
"""
from __future__ import annotations

import asyncio

import agent.service as svc


def test_compose_turn_prompt_injects_context_and_returns_slugs(monkeypatch):
    monkeypatch.setattr(
        svc, "retrieve_for_turn_rich",
        lambda text: ("<vault_context>\nGROUNDING\n</vault_context>", ["phase-1-v3"],
                      {"phase-1-v3": "GROUNDING"}),
    )
    query, slugs = asyncio.run(svc._compose_turn_prompt("42", "how do I stay consistent"))
    assert "<user_id>42</user_id>" in query
    assert "<vault_context>" in query and "GROUNDING" in query
    assert "how do I stay consistent" in query
    # Context comes before the user's message so the model reads it first.
    assert query.index("GROUNDING") < query.index("how do I stay consistent")
    assert slugs == ["phase-1-v3"]


def test_compose_turn_prompt_omits_block_when_no_context(monkeypatch):
    monkeypatch.setattr(svc, "retrieve_for_turn_rich", lambda text: ("", [], {}))
    query, slugs = asyncio.run(svc._compose_turn_prompt("42", "hi"))
    assert "<vault_context>" not in query
    assert "<user_id>42</user_id>" in query
    assert query.rstrip().endswith("hi")
    assert slugs == []


def test_merge_sources_unions_preserving_first_seen_order():
    merged = svc._merge_sources(["a", "b"], ["b", "c"], ["a"])
    assert merged == ["a", "b", "c"]
