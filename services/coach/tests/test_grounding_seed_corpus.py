from eval.grounding.seed_corpus_from_calibration import build_records, classify


def test_classify_needs_data_is_catch():
    assert classify("Need more data (no story anchor).") == "catch"
    assert classify("Invalid, need more data") == "catch"


def test_classify_intent_contradiction_is_correction():
    assert classify("No, it wasn't avoidance work. It was a tactic.") == "correction"
    assert classify("") == "correction"  # demote with no note -> still a correction signal


def test_build_records_only_demotes_with_fields():
    cases = [
        {"n": 1, "claim": "endorsed read", "type": "interpretation", "conf": 8, "entry": "e1"},
        {"n": 2, "claim": "wrong read", "type": "interpretation", "conf": 6, "entry": "e2"},
        {"n": 3, "claim": "thin anchor", "type": "conceptual", "conf": None, "entry": "e3"},
    ]
    labels = {
        "1": {"verdict": "state_as_read", "correction": None},
        "2": {"verdict": "demote", "correction": "Different point — a tactic."},
        "3": {"verdict": "demote", "correction": "Need more data."},
    }
    recs = build_records(cases, labels, date="2026-06-06")
    assert [r["claim_id"] for r in recs] == ["vault-cal-02", "vault-cal-03"]
    assert recs[0]["signal"] == "correction" and recs[0]["ts"] == "2026-06-06"
    assert recs[1]["signal"] == "catch"
    assert recs[0]["user_correction"] == "Different point — a tactic."
