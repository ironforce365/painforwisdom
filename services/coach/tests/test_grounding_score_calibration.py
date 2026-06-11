import json

from eval.grounding.score_calibration import (
    judge_action,
    load_labels_json,
    parse_md_verdicts,
    score,
)
from eval.grounding.types import Action, ClaimType, Verdict


def test_load_labels_json_extracts_valid_verdicts(tmp_path):
    p = tmp_path / "labels.json"
    p.write_text(json.dumps({"labels": {
        "1": {"verdict": "demote", "agree": False},
        "2": {"verdict": "assert", "agree": True},
        "3": {"verdict": "bogus"},
    }}), encoding="utf-8")
    assert load_labels_json(p) == {1: "demote", 2: "assert"}


def test_parse_md_verdicts_picks_filled_rows():
    md = (
        "| # | Entry | Source | Coach claim | Type/conf | Your verdict |\n"
        "|---|---|---|---|---|---|\n"
        "| 1 | e | s | c | interpretation conf=7 | demote |\n"
        "| 2 | e | s | c | fact | assert |\n"
        "| 3 | e | s | c | interpretation conf=6 |  |\n"
        "| 4 | e | s | c | interpretation conf=9 | state_as_read |\n"
    )
    assert parse_md_verdicts(md) == {1: "demote", 2: "assert", 4: "state_as_read"}


def test_judge_action_fact_grounded_asserts():
    case = {"n": 1, "type": "fact", "conf": None, "claim": "You missed two runs.", "source_full": "missed Thu/Fri", "entry": "e"}

    def judge_fn(claims, sources):
        return [Verdict(claims[0].id, ClaimType.FACT, grounded=True, contradicts=False, rationale="")]

    assert judge_action(case, temperature=6, judge_fn=judge_fn) is Action.ASSERT


def test_judge_action_ungrounded_fact_demotes():
    case = {"n": 2, "type": "fact", "conf": None, "claim": "You hated yourself.", "source_full": "missed Thu/Fri", "entry": "e"}

    def judge_fn(claims, sources):
        return [Verdict(claims[0].id, ClaimType.FACT, grounded=False, contradicts=False, rationale="")]

    assert judge_action(case, temperature=6, judge_fn=judge_fn) is Action.DEMOTE


def test_score_computes_agreement():
    cases = [
        {"n": 1, "type": "fact", "conf": None, "claim": "x", "source_full": "s", "entry": "e"},
        {"n": 2, "type": "interpretation", "conf": 9, "claim": "y", "source_full": "s", "entry": "e"},
    ]
    verdicts = {1: "assert", 2: "demote"}  # human says demote #2

    def judge_fn(claims, sources):
        # #1 grounded fact -> assert (match); #2 interpretation, not contradicting,
        # conf 9 >= temp 6 -> state_as_read (mismatch vs human demote)
        c = claims[0]
        if c.type is ClaimType.FACT:
            return [Verdict(c.id, ClaimType.FACT, grounded=True, contradicts=False, rationale="")]
        return [Verdict(c.id, ClaimType.INTERPRETATION, grounded=False, contradicts=False, rationale="")]

    report = score(cases, verdicts, temperature=6, judge_fn=judge_fn)
    by_n = {r["n"]: r for r in report["rows"]}
    assert by_n[1]["match"] is True
    assert by_n[2]["judge"] == "state_as_read" and by_n[2]["human"] == "demote" and by_n[2]["match"] is False
    assert report["labeled"] == 2 and report["agree"] == 1 and report["agreement"] == 0.5
