"""Source-typed grounding: a FACT about the user may only be asserted when a
MEMORY source (conversation-derived) entails it. A doctrine source can ground a
conceptual claim but NEVER a biographical fact about the interlocutor.

This is the fix for the "four months of recovery" leak: the coach pulled a
biographical fact from the vault (doctrine) and asserted it about the user. Under
source typing that fact is grounded only by doctrine → demoted to a question.
"""
from eval.grounding.corpus import RegressionCorpus
from eval.grounding.decide import decide
from eval.grounding.gate import run_gate
from eval.grounding.types import (
    KIND_DOCTRINE,
    KIND_MEMORY,
    Action,
    ClaimType,
    Source,
    Verdict,
)


def V(dt, *, grounded=False, grounded_by=None, contradicts=False):
    return Verdict(
        claim_id="c",
        derived_type=dt,
        grounded=grounded,
        grounded_by=grounded_by or [],
        contradicts=contradicts,
        rationale="",
    )


# ---- decide(), typed mode -------------------------------------------------

DM = {"D1": KIND_DOCTRINE, "M1": KIND_MEMORY}


def test_fact_grounded_only_by_doctrine_is_demoted():
    # the "four months" shape: entailed by doctrine, not by memory
    d = decide(V(ClaimType.FACT, grounded=True, grounded_by=["D1"]),
               confidence=None, temperature=5, source_kinds=DM)
    assert d.action is Action.DEMOTE


def test_fact_grounded_by_memory_asserts():
    d = decide(V(ClaimType.FACT, grounded=True, grounded_by=["M1"]),
               confidence=None, temperature=5, source_kinds=DM)
    assert d.action is Action.ASSERT


def test_fact_grounded_by_both_asserts():
    d = decide(V(ClaimType.FACT, grounded=True, grounded_by=["D1", "M1"]),
               confidence=None, temperature=5, source_kinds=DM)
    assert d.action is Action.ASSERT


def test_fact_typed_but_no_grounding_demoted():
    d = decide(V(ClaimType.FACT, grounded=False, grounded_by=[]),
               confidence=None, temperature=5, source_kinds=DM)
    assert d.action is Action.DEMOTE


def test_conceptual_grounded_by_doctrine_asserts():
    d = decide(V(ClaimType.CONCEPTUAL, grounded=True, grounded_by=["D1"]),
               confidence=None, temperature=5, source_kinds=DM)
    assert d.action is Action.ASSERT


def test_conceptual_typed_ungrounded_demoted():
    d = decide(V(ClaimType.CONCEPTUAL, grounded=False, grounded_by=[]),
               confidence=None, temperature=5, source_kinds=DM)
    assert d.action is Action.DEMOTE


def test_interpretation_unaffected_by_typing():
    d = decide(V(ClaimType.INTERPRETATION), confidence=8, temperature=6, source_kinds=DM)
    assert d.action is Action.STATE_AS_READ


# ---- legacy mode preserved (no memory/doctrine kinds) ---------------------

def test_legacy_untyped_sources_use_grounded_flag():
    # kinds that aren't memory/doctrine -> legacy path -> trust verdict.grounded
    legacy_kinds = {"S1": "debrief"}
    assert decide(V(ClaimType.FACT, grounded=True), confidence=None,
                  temperature=5, source_kinds=legacy_kinds).action is Action.ASSERT
    assert decide(V(ClaimType.FACT, grounded=False), confidence=None,
                  temperature=5, source_kinds=legacy_kinds).action is Action.DEMOTE


def test_no_source_kinds_is_legacy():
    assert decide(V(ClaimType.FACT, grounded=True), confidence=None,
                  temperature=5).action is Action.ASSERT


# ---- gate-level regression: the biography leak ----------------------------

def test_gate_demotes_doctrine_only_biography(tmp_path):
    """A biographical fact (cite=D1, doctrine) is demoted; a principle (cite=D1)
    and a memory-grounded fact (cite=M1) survive. The real bug, end to end."""
    draft = (
        "[[claim id=c1 type=fact cite=M1]] You said the Achilles has bugged you all week.\n"
        "[[claim id=c2 type=fact cite=D1]] That earlier round cost you four months of recovery.\n"
        "[[claim id=c3 type=conceptual cite=D1]] Pain that quiets under load can mask accumulating damage.\n"
        "What does it feel like at rest?"
    )
    sources = [
        Source(id="D1", tier=2, kind=KIND_DOCTRINE, text="Pain that fades during effort can mask tendon damage that is still accumulating."),
        Source(id="M1", tier=1, kind=KIND_MEMORY, text="User said the left Achilles has bothered them before and after runs all week."),
    ]
    verdicts = [
        Verdict("c1", ClaimType.FACT, grounded=True, grounded_by=["M1"], contradicts=False, rationale="memory entails"),
        Verdict("c2", ClaimType.FACT, grounded=True, grounded_by=["D1"], contradicts=False, rationale="only doctrine mentions four months"),
        Verdict("c3", ClaimType.CONCEPTUAL, grounded=True, grounded_by=["D1"], contradicts=False, rationale="doctrine entails principle"),
    ]
    result = run_gate(
        draft, sources, temperature=5,
        judge_fn=lambda claims, srcs: verdicts,
        rewrite_fn=lambda claim, srcs: "Has a flare like this cost you a long recovery before?",
        corpus=RegressionCorpus(tmp_path / "c"),
    )
    assert "Achilles has bugged you all week." in result.message      # memory-grounded fact survives
    assert "four months of recovery" not in result.message            # doctrine-only biography removed
    assert "Has a flare like this cost you a long recovery before?" in result.message  # demoted to question
    assert "mask accumulating damage." in result.message              # principle survives
    assert "What does it feel like at rest?" in result.message        # passthrough kept
    assert "c2" in result.logged_ids
