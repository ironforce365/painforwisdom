import eval.grounding.rewriter as rw
from eval.grounding.types import Claim, ClaimType


def test_demote_returns_question(monkeypatch):
    monkeypatch.setattr(rw, "call_llm", lambda **kw: "Were you punishing yourself, or just noticing the heaviness?")
    c = Claim(id="c1", type=ClaimType.FACT, text="You were punishing yourself.")
    q = rw.demote_to_question(c, sources=[])
    assert q.endswith("?")
    assert "punishing" in q.lower()


def test_appends_question_mark_if_missing(monkeypatch):
    monkeypatch.setattr(rw, "call_llm", lambda **kw: "Did the missed runs weigh on you")
    c = Claim(id="c1", type=ClaimType.FACT, text="You felt guilty.")
    assert rw.demote_to_question(c, sources=[]).endswith("?")


def test_strips_wrapping_quotes(monkeypatch):
    monkeypatch.setattr(rw, "call_llm", lambda **kw: '"How did the week feel?"')
    c = Claim(id="c1", type=ClaimType.INTERPRETATION, text="x")
    assert rw.demote_to_question(c, sources=[]) == "How did the week feel?"
