"""Seed a regression corpus from the human-labeled calibration set.

Every case Gonzalo marked `demote` becomes a corpus record — the canonical set the
gate must keep getting right (or, for the contradiction bucket, the set the gate
*cannot* catch and the validation loop must own). Two signals:

- ``correction``: a lived-intent contradiction. The read was plausible from the
  source but wrong about what Gonzalo actually meant; his words are the correction.
- ``catch``: insufficient grounding (no/thin story anchor) — "need more data".

Idempotent: rewrites a dedicated calibration corpus dir from the labels each run, so
re-running after Gonzalo revises labels just refreshes it. Timestamps are caller-
supplied (--date) — this module, like corpus.py, never invents them.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from eval.grounding.corpus import RegressionCorpus

_NEEDS_DATA = re.compile(r"need more data|no anchor|no story anchor", re.IGNORECASE)


def classify(correction: str) -> str:
    """correction text -> corpus signal."""
    return "catch" if _NEEDS_DATA.search(correction or "") else "correction"


def build_records(cases: list[dict], labels: dict[str, dict], *, date: str) -> list[dict]:
    by_n = {c["n"]: c for c in cases}
    records: list[dict] = []
    for k, lab in labels.items():
        if lab.get("verdict") != "demote":
            continue
        n = int(k)
        case = by_n.get(n, {})
        correction = lab.get("correction") or ""
        records.append({
            "ts": date,
            "signal": classify(correction),
            "claim_id": f"vault-cal-{n:02d}",
            "claim_text": case.get("claim", ""),
            "claim_type": case.get("type"),
            "confidence": case.get("conf"),
            "entry": case.get("entry"),
            "user_correction": correction,
            "source": "calibration",
        })
    return records


def _main() -> None:
    ap = argparse.ArgumentParser(description="Seed regression corpus from calibration labels.")
    ap.add_argument("--cases", default="services/coach/eval/grounding/CALIBRATION_VAULT.jsonl")
    ap.add_argument("--labels", default="services/coach/eval/grounding/CALIBRATION_VAULT.labels.json")
    ap.add_argument("--out", default="services/coach/eval/grounding/calibration_corpus")
    ap.add_argument("--date", required=True, help="ISO date for the records (caller-supplied; corpus never invents)")
    args = ap.parse_args()

    cases = [json.loads(ln) for ln in Path(args.cases).read_text(encoding="utf-8").splitlines() if ln.strip()]
    labels = json.loads(Path(args.labels).read_text(encoding="utf-8"))["labels"]
    records = build_records(cases, labels, date=args.date)

    out = Path(args.out)
    # idempotent: clear the derived corpus before re-seeding
    for fn in ("corpus.jsonl", "corpus.md"):
        p = out / fn
        if p.exists():
            p.unlink()
    corpus = RegressionCorpus(out)
    for r in records:
        corpus.append(r)

    by_sig: dict[str, int] = {}
    for r in records:
        by_sig[r["signal"]] = by_sig.get(r["signal"], 0) + 1
    print(f"seeded {len(records)} records -> {out}  ({by_sig})")


if __name__ == "__main__":
    _main()
