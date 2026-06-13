"""Offline-or-live harness for the validation detector.

Runs ``validation_cases/cases.json`` through ``detect`` and scores exact-match
per open item. With the default (real) LLM seam this is the live calibration of
Stream 2's riskiest assumption — semantic matching of user replies to open
validations. Pass a fake ``llm_fn`` (tests) to run offline.

    PYTHONPATH=services/coach python3 -m eval.grounding.validation_harness
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Callable

from eval.grounding.validation_detector import detect

_CASES = Path(__file__).parent / "validation_cases" / "cases.json"


def load_cases(path: Path = _CASES) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))["cases"]


def run_cases(cases: list[dict], *, llm_fn: Callable | None = None,
              model: str = "claude-sonnet-4-6") -> dict:
    """Run every case; return {results: [...], items_total, items_correct}."""
    kw = {"model": model}
    if llm_fn is not None:
        kw["llm_fn"] = llm_fn
    results = []
    total = correct = 0
    for case in cases:
        outcomes = detect(case["user_reply"], case["open_items"], **kw)
        got = {o["claim_id"]: o["outcome"] for o in outcomes}
        corrections = {o["claim_id"]: o["correction_text"] for o in outcomes}
        per_item = []
        for cid, want in case["expected"].items():
            ok = got.get(cid) == want
            total += 1
            correct += int(ok)
            per_item.append({"claim_id": cid, "expected": want,
                             "got": got.get(cid, "(missing)"), "ok": ok,
                             "correction_text": corrections.get(cid, "")})
        results.append({"id": case["id"], "items": per_item,
                        "ok": all(i["ok"] for i in per_item)})
    return {"results": results, "items_total": total, "items_correct": correct}


def _main() -> None:
    ap = argparse.ArgumentParser(description="Calibrate the validation detector.")
    ap.add_argument("--cases", default=str(_CASES))
    ap.add_argument("--model", default="claude-sonnet-4-6")
    args = ap.parse_args()

    report = run_cases(load_cases(Path(args.cases)), model=args.model)
    for r in report["results"]:
        flag = "✓" if r["ok"] else "✗"
        print(f"{flag} {r['id']}")
        for i in r["items"]:
            mark = "  ✓" if i["ok"] else f"  ✗ expected={i['expected']}"
            print(f"   {i['claim_id']}: got={i['got']}{mark}")
            if i["correction_text"]:
                print(f"      correction: {i['correction_text'][:90]}")
    print(f"\nitems: {report['items_correct']}/{report['items_total']} correct")


if __name__ == "__main__":
    _main()
