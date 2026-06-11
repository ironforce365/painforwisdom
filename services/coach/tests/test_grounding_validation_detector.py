import json

from eval.grounding.validation_detector import detect

OPEN_ITEMS = [
    {
        "claim_id": "c1",
        "claim_text": "This reads like self-punishment for the missed sessions.",
        "question": "Were you pushing to punish yourself, or was it something else?",
        "action": "demote",
    },
    {
        "claim_id": "c2",
        "claim_text": "The bargaining was a permission structure.",
        "question": "",
        "action": "state_as_read",
    },
]


def _fake_llm(payload):
    def fake(*, system, user, model="m"):
        return json.dumps(payload)
    return fake


def test_detect_classifies_confirmed_and_corrected():
    payload = {"outcomes": [
        {"claim_id": "c1", "outcome": "corrected",
         "correction_text": "No — it was a tactic to bank miles before the storm."},
        {"claim_id": "c2", "outcome": "confirmed", "correction_text": ""},
    ]}
    out = detect("No, not punishment — I was banking miles before the storm. "
                 "And yes, the 10-minutes thing is exactly a permission structure.",
                 OPEN_ITEMS, llm_fn=_fake_llm(payload))
    by_id = {o["claim_id"]: o for o in out}
    assert by_id["c1"]["outcome"] == "corrected"
    assert "banking miles" in by_id["c1"]["correction_text"] or by_id["c1"]["correction_text"]
    assert by_id["c2"]["outcome"] == "confirmed"


def test_detect_empty_open_items_skips_llm():
    def boom(**_kw):
        raise AssertionError("llm must not be called with no open items")
    assert detect("anything", [], llm_fn=boom) == []


def test_detect_malformed_llm_output_degrades_to_unaddressed():
    def garbage(**_kw):
        return "not json at all"
    out = detect("some reply", OPEN_ITEMS, llm_fn=garbage)
    assert [o["outcome"] for o in out] == ["unaddressed", "unaddressed"]


def test_detect_unknown_claim_ids_dropped_and_missing_filled():
    payload = {"outcomes": [
        {"claim_id": "c1", "outcome": "confirmed", "correction_text": ""},
        {"claim_id": "ghost", "outcome": "corrected", "correction_text": "x"},
    ]}
    out = detect("yes that's right", OPEN_ITEMS, llm_fn=_fake_llm(payload))
    by_id = {o["claim_id"]: o for o in out}
    assert set(by_id) == {"c1", "c2"}          # ghost dropped, c2 filled in
    assert by_id["c2"]["outcome"] == "unaddressed"


def test_detect_invalid_outcome_coerced_to_unaddressed():
    payload = {"outcomes": [
        {"claim_id": "c1", "outcome": "maybe-kinda", "correction_text": ""},
        {"claim_id": "c2", "outcome": "confirmed", "correction_text": ""},
    ]}
    out = detect("hmm", OPEN_ITEMS, llm_fn=_fake_llm(payload))
    by_id = {o["claim_id"]: o for o in out}
    assert by_id["c1"]["outcome"] == "unaddressed"
    assert by_id["c2"]["outcome"] == "confirmed"
