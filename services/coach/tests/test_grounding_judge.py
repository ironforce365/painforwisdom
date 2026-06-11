import json

import eval.grounding.judge as gj
from eval.grounding.types import Claim, ClaimType, Source


def test_judge_claims_parses_batch(monkeypatch):
    claims = [
        Claim(id="c1", type=ClaimType.FACT, text="You missed two runs.", cites=["S1"]),
        Claim(id="c2", type=ClaimType.FACT, text="You were punishing yourself.", cites=[]),
    ]
    sources = [Source(id="S1", tier=1, kind="debrief", text="missed Thursday and Friday")]
    fake = json.dumps({"verdicts": [
        {"claim_id": "c1", "derived_type": "fact", "grounded": True, "contradicts": False, "rationale": "S1 entails"},
        {"claim_id": "c2", "derived_type": "fact", "grounded": False, "contradicts": False, "rationale": "no source"},
    ]})
    monkeypatch.setattr(gj, "call_llm", lambda **kw: fake)
    verdicts = gj.judge_claims(claims, sources)
    assert {v.claim_id: v.grounded for v in verdicts} == {"c1": True, "c2": False}
    assert verdicts[1].derived_type is ClaimType.FACT


def test_uncited_conceptual_rederived_as_interpretation(monkeypatch):
    claims = [Claim(id="c3", type=ClaimType.CONCEPTUAL, text="Studies show X.", cites=[])]
    fake = json.dumps({"verdicts": [
        {"claim_id": "c3", "derived_type": "interpretation", "grounded": False, "contradicts": False, "rationale": "uncited"},
    ]})
    monkeypatch.setattr(gj, "call_llm", lambda **kw: fake)
    v = gj.judge_claims(claims, [])[0]
    assert v.derived_type is ClaimType.INTERPRETATION


def test_empty_claims_no_call(monkeypatch):
    called = {"n": 0}

    def boom(**kw):
        called["n"] += 1
        return "{}"

    monkeypatch.setattr(gj, "call_llm", boom)
    assert gj.judge_claims([], []) == []
    assert called["n"] == 0  # no LLM call when nothing to judge


def test_handles_prose_wrapped_json(monkeypatch):
    fake = 'Sure, here is the result:\n{"verdicts": [{"claim_id": "c1", "derived_type": "fact", "grounded": true, "contradicts": false, "rationale": "ok"}]}\nDone.'
    monkeypatch.setattr(gj, "call_llm", lambda **kw: fake)
    claims = [Claim(id="c1", type=ClaimType.FACT, text="x", cites=["S1"])]
    v = gj.judge_claims(claims, [Source(id="S1", tier=1, kind="debrief", text="x")])[0]
    assert v.grounded is True
