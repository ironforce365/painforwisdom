from eval.grounding.decide import decide
from eval.grounding.types import Action, ClaimType, Verdict


def V(dt, grounded=False, contradicts=False):
    return Verdict(claim_id="c", derived_type=dt, grounded=grounded, contradicts=contradicts, rationale="")


def test_grounded_fact_asserts():
    assert decide(V(ClaimType.FACT, grounded=True), confidence=None, temperature=5).action is Action.ASSERT


def test_ungrounded_fact_demoted_regardless_of_temperature():
    # "punishing yourself": fact, not grounded -> always demote, even at temp 1
    assert decide(V(ClaimType.FACT, grounded=False), confidence=None, temperature=1).action is Action.DEMOTE


def test_confident_interpretation_states_as_read():
    assert decide(V(ClaimType.INTERPRETATION), confidence=7, temperature=6).action is Action.STATE_AS_READ


def test_interpretation_at_threshold_states_as_read():
    # confidence >= temperature -> assert as read (boundary)
    assert decide(V(ClaimType.INTERPRETATION), confidence=6, temperature=6).action is Action.STATE_AS_READ


def test_shaky_interpretation_demoted():
    assert decide(V(ClaimType.INTERPRETATION), confidence=4, temperature=6).action is Action.DEMOTE


def test_contradicting_interpretation_demoted_even_if_confident():
    assert decide(V(ClaimType.INTERPRETATION, contradicts=True), confidence=10, temperature=1).action is Action.DEMOTE


def test_interpretation_missing_confidence_treated_as_least():
    # no confidence -> least confident -> demote unless temperature is 1
    assert decide(V(ClaimType.INTERPRETATION), confidence=None, temperature=5).action is Action.DEMOTE
    assert decide(V(ClaimType.INTERPRETATION), confidence=None, temperature=1).action is Action.STATE_AS_READ


def test_grounded_conceptual_asserts():
    assert decide(V(ClaimType.CONCEPTUAL, grounded=True), confidence=None, temperature=5).action is Action.ASSERT


def test_ungrounded_conceptual_demoted():
    assert decide(V(ClaimType.CONCEPTUAL, grounded=False), confidence=None, temperature=5).action is Action.DEMOTE
