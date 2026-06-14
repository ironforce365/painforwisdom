"""Stream 2 plumbing: source text for the judge + claim-contract prompt append."""
from __future__ import annotations

import agent.retrieval as r
from agent.prompts import compose_system_prompt


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


def test_retrieve_for_turn_rich_returns_slug_text_map():
    r.set_retriever_for_tests(
        _FakeRetriever(
            [
                _FakeNode("Lock the long run.", "phase-1-v3", 1.0),
                _FakeNode("Show up imperfectly.", "showing-up", 0.6),
                _FakeNode("   ", "blank-chunk", 0.5),
            ]
        )
    )
    block, slugs, slug_text = r.retrieve_for_turn_rich("consistency")
    assert "Lock the long run." in block
    assert slugs == ["phase-1-v3", "showing-up", "blank-chunk"]
    assert slug_text == {
        "phase-1-v3": "Lock the long run.",
        "showing-up": "Show up imperfectly.",
    }  # blank chunk carries no text for the judge


def test_retrieve_for_turn_still_two_tuple():
    r.set_retriever_for_tests(_FakeRetriever([_FakeNode("T.", "s1", 1.0)]))
    block, slugs = r.retrieve_for_turn("q")
    assert slugs == ["s1"]


def test_retrieve_doctrine_for_turn_returns_block_slugs_text():
    r.set_doctrine_retriever_for_tests(
        _FakeRetriever([
            _FakeNode("Pain that quiets under load can mask damage.", "body-literacy", 1.0),
            _FakeNode("Recovery is a strategy, not a concession.", "recovery", 0.7),
        ])
    )
    block, slugs, joined = r.retrieve_doctrine_for_turn("achilles")
    assert "<doctrine>" in block and "mask damage." in block
    assert "teaching material" in block.lower()  # framed as doctrine, not biography
    assert slugs == ["body-literacy", "recovery"]
    assert "Pain that quiets" in joined and "<doctrine>" not in joined


def test_retrieve_doctrine_for_turn_swallows_errors():
    class _Boom:
        def retrieve(self, q):
            raise RuntimeError("no doctrine index")

    r.set_doctrine_retriever_for_tests(_Boom())
    assert r.retrieve_doctrine_for_turn("x") == ("", [], "")


def test_retrieve_for_turn_rich_swallows_errors():
    class _Boom:
        def retrieve(self, q):
            raise RuntimeError("index gone")

    r.set_retriever_for_tests(_Boom())
    assert r.retrieve_for_turn_rich("x") == ("", [], {})


def test_compose_system_prompt_without_claims_is_base_prompt():
    base = compose_system_prompt(claims=False)
    assert "endurance virtual coach" in base
    assert "[[claim" not in base  # contract absent => no tag instructions


def test_compose_system_prompt_with_claims_appends_contract():
    full = compose_system_prompt(claims=True)
    assert full.startswith(compose_system_prompt(claims=False))
    assert "[[claim" in full
    assert "conf=" in full
    # doctrine/memory world: facts cite M1 (memory), principles cite D1 (doctrine)
    assert "cite=M1" in full
    assert "cite=D1" in full
    assert "<about_this_user>" in full and "<doctrine>" in full
