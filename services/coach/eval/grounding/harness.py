"""Offline precision harness: run the gate over self-labeling fixtures and score
each claim's action against the planted expectation.

Unit tests inject a judge (offline). ``python -m eval.grounding.harness`` runs
the REAL subscription judge against the fixtures and prints a report.
"""
from __future__ import annotations

import argparse

from .fixtures import load_fixtures
from .gate import run_gate
from .judge import judge_claims
from .rewriter import demote_to_question
from .types import Action, Source

_ACTION_BY_NAME = {
    "assert": Action.ASSERT,
    "demote": Action.DEMOTE,
    "state_as_read": Action.STATE_AS_READ,
}


def _expected_action(fx: dict, claim_id: str):
    for name, action in _ACTION_BY_NAME.items():
        if claim_id in fx["expect"].get(name, []):
            return action
    return None


def score_fixture(fx: dict, *, temperature: int, judge_fn=judge_claims, rewrite_fn=demote_to_question) -> dict:
    sources = [Source(**s) for s in fx["sources"]]
    result = run_gate(fx["draft"], sources, temperature=temperature, judge_fn=judge_fn, rewrite_fn=rewrite_fn)
    by_id = {d.claim_id: d.action for d in result.decisions}
    correct = total = 0
    mismatches: list[dict] = []
    for cid, got in by_id.items():
        exp = _expected_action(fx, cid)
        if exp is None:
            continue
        total += 1
        if got is exp:
            correct += 1
        else:
            mismatches.append({"claim_id": cid, "expected": exp.value, "got": got.value})
    return {
        "id": fx["id"],
        "correct": correct,
        "total": total,
        "agreement": (correct / total) if total else 0.0,
        "mismatches": mismatches,
    }


def run_all(*, temperature: int, judge_fn=judge_claims) -> list[dict]:
    return [score_fixture(fx, temperature=temperature, judge_fn=judge_fn) for fx in load_fixtures()]


def _main() -> None:
    ap = argparse.ArgumentParser(description="Grounding precision harness (real subscription judge).")
    ap.add_argument("--temperature", type=int, default=6)
    args = ap.parse_args()
    reports = run_all(temperature=args.temperature)
    tot = sum(r["total"] for r in reports)
    ok = sum(r["correct"] for r in reports)
    for r in reports:
        print(f"{r['id']}: {r['correct']}/{r['total']} agreement={r['agreement']:.2f} mismatches={r['mismatches']}")
    print(f"OVERALL: {ok}/{tot} agreement={(ok / tot) if tot else 0:.2f}")


if __name__ == "__main__":
    _main()
