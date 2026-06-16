"""In-process vault pre-retrieval for the coach agent.

Regression coverage for the 2026-06-04 grounding incident: the agent never
called the search_vault MCP tool, so every turn answered from general knowledge
with retrieved_sources: []. The retrieval stack was healthy the whole time — the
model just never invoked it. The fix retrieves deterministically in-process and
injects the result into the turn prompt, guaranteeing a grounding floor.
"""
from __future__ import annotations

import agent.retrieval as r


class _FakeNode:
    def __init__(self, text: str, slug: str, score: float):
        self._text = text
        self.metadata = {"slug": slug}
        self.score = score

    def get_content(self) -> str:
        return self._text


class _FakeRetriever:
    def __init__(self, nodes):
        self._nodes = nodes

    def retrieve(self, query: str):
        return self._nodes


class _BoomRetriever:
    def retrieve(self, query: str):
        raise RuntimeError("index gone")


def test_format_vault_context_empty_returns_blank():
    assert r.format_vault_context([]) == ""


def test_format_vault_context_wraps_text_and_hides_slugs():
    block = r.format_vault_context(
        [
            {"text": "Lock one long run per week.", "source": "phase-1-v3", "score": 1.0},
            {"text": "Repetition over intensity.", "source": "repetition-over-intensity", "score": 0.7},
        ]
    )
    assert "<vault_context>" in block and "</vault_context>" in block
    # Chunk text must be present...
    assert "Lock one long run per week." in block
    assert "Repetition over intensity." in block
    # ...but the source slugs must NOT leak into model context (rule #1: never cite).
    assert "phase-1-v3" not in block
    assert "repetition-over-intensity" not in block


def test_format_vault_context_skips_blank_chunks():
    assert r.format_vault_context([{"text": "   ", "source": "x", "score": 1.0}]) == ""


def test_retrieve_for_turn_returns_context_and_slugs():
    r.set_retriever_for_tests(
        _FakeRetriever(
            [
                _FakeNode("Lock the long run.", "phase-1-v3", 1.0),
                _FakeNode("Show up imperfectly.", "showing-up-imperfectly", 0.6),
            ]
        )
    )
    block, slugs = r.retrieve_for_turn("how do I stay consistent")
    assert "Lock the long run." in block
    assert slugs == ["phase-1-v3", "showing-up-imperfectly"]


def test_retrieve_for_turn_swallows_errors_to_protect_the_turn():
    # A retrieval failure must degrade to no-context, never take down the coach.
    r.set_retriever_for_tests(_BoomRetriever())
    block, slugs = r.retrieve_for_turn("anything")
    assert block == ""
    assert slugs == []


def test_search_vault_warm_selects_doctrine_vs_raw():
    # The warm tool helper picks the doctrine retriever when asked (gate on) and
    # the raw-vault retriever otherwise — reusing the cached singletons, not a
    # fresh per-turn subprocess.
    r.set_doctrine_retriever_for_tests(_FakeRetriever([_FakeNode("Principle.", "body-literacy", 1.0)]))
    r.set_retriever_for_tests(_FakeRetriever([_FakeNode("Raw entry.", "2026-03-16-x", 0.9)]))

    doc = r.search_vault_warm("q", doctrine=True)
    assert doc == [{"text": "Principle.", "source": "body-literacy", "score": 1.0}]
    raw = r.search_vault_warm("q", doctrine=False)
    assert raw == [{"text": "Raw entry.", "source": "2026-03-16-x", "score": 0.9}]


def test_search_vault_warm_swallows_errors():
    # Same protection as pre-retrieval: a mid-turn dig-deeper failure degrades to
    # no results rather than surfacing an exception into the agent loop.
    r.set_doctrine_retriever_for_tests(_BoomRetriever())
    assert r.search_vault_warm("q", doctrine=True) == []
