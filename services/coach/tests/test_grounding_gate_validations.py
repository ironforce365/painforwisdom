"""run_gate records open validations for DEMOTE and STATE_AS_READ decisions."""
from eval.grounding.gate import run_gate
from eval.grounding.types import ClaimType, Source, Verdict
from eval.grounding.validations import PendingValidations

SOURCES = [Source(id="S1", tier=1, kind="vault_entry", text="ran 6 miles in the storm")]

DRAFT = (
    "[[claim id=c1 type=fact cite=S1]] You ran 6 miles in the storm.\n"
    "[[claim id=c2 type=interpretation conf=8]] You wanted the storm as a test.\n"
    "[[claim id=c3 type=interpretation conf=3]] You were punishing yourself.\n"
    "Keep it up."
)


def _judge(claims, sources):
    return [
        Verdict("c1", ClaimType.FACT, grounded=True, contradicts=False, rationale=""),
        Verdict("c2", ClaimType.INTERPRETATION, grounded=False, contradicts=False, rationale=""),
        Verdict("c3", ClaimType.INTERPRETATION, grounded=False, contradicts=False, rationale=""),
    ]


def _rewrite(claim, sources):
    return f"Q[{claim.id}]?"


def test_gate_opens_validations_for_demote_and_state_as_read(tmp_path):
    pv = PendingValidations(tmp_path)
    result = run_gate(
        DRAFT, SOURCES, temperature=6, judge_fn=_judge, rewrite_fn=_rewrite,
        validations=pv, ts="2026-06-10T23:00:00", thread_id="t1", user_id="u1",
    )
    opens = {i["claim_id"]: i for i in pv.list_open("t1")}
    # c1 asserted -> no validation; c2 stated-as-read + c3 demoted -> opened
    assert set(opens) == {"c2", "c3"}
    assert opens["c2"]["action"] == "state_as_read"
    assert opens["c2"]["claim_text"] == "You wanted the storm as a test."
    assert opens["c2"]["ts"] == "2026-06-10T23:00:00"
    assert opens["c3"]["action"] == "demote"
    assert opens["c3"]["question"] == "Q[c3]?"
    assert "My read: You wanted the storm as a test." in result.message


def test_gate_without_validations_store_unchanged(tmp_path):
    result = run_gate(DRAFT, SOURCES, temperature=6, judge_fn=_judge, rewrite_fn=_rewrite)
    assert "Q[c3]?" in result.message  # behaves exactly as before
