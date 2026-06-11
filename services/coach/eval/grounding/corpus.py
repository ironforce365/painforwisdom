"""Regression corpus: the persisted ledger of grounding signals.

Three inflows (spec sec 3): catches (gate-blocked assertions), corrections
(user disagreements), validations (user promotions). jsonl is the machine store;
corpus.md is a human-readable mirror. Timestamps are supplied by the caller --
this module never invents them.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional


class RegressionCorpus:
    def __init__(self, directory: Path | str):
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.jsonl = self.dir / "corpus.jsonl"
        self.md = self.dir / "corpus.md"

    def append(self, record: dict) -> None:
        with self.jsonl.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        line = (
            f"- **{record.get('signal', '?')}** `{record.get('claim_id', '?')}` — "
            f"{str(record.get('claim_text', ''))[:160]}"
        )
        if record.get("demoted_question"):
            line += f"  → _{record['demoted_question']}_"
        if record.get("user_correction"):
            line += f"  ✗ correction: {record['user_correction']}"
        with self.md.open("a", encoding="utf-8") as f:
            f.write(line + "\n")

    def load(self, signal: Optional[str] = None) -> list[dict]:
        if not self.jsonl.exists():
            return []
        out: list[dict] = []
        for ln in self.jsonl.read_text(encoding="utf-8").splitlines():
            if ln.strip():
                rec = json.loads(ln)
                if signal is None or rec.get("signal") == signal:
                    out.append(rec)
        return out
