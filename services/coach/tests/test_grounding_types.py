from eval.grounding.types import Action, Claim, ClaimType, Decision, Source, Verdict


def test_types_construct():
    c = Claim(id="c1", type=ClaimType.FACT, text="You missed two runs.", cites=["S1"], confidence=None)
    s = Source(id="S1", tier=1, kind="debrief", text="missed Thu/Fri")
    v = Verdict(claim_id="c1", derived_type=ClaimType.FACT, grounded=True, contradicts=False, rationale="ok")
    d = Decision(claim_id="c1", action=Action.ASSERT, question=None)
    assert c.type is ClaimType.FACT
    assert c.cites == ["S1"]
    assert s.tier == 1
    assert v.grounded
    assert d.action is Action.ASSERT
    assert d.question is None
