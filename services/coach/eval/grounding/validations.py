"""Pending-validation store: the coach's open questions, remembered.

When the gate demotes a claim to a question (or states it as a read), the coach
has implicitly *asked* the user something. This store remembers those open items
per thread so the user's next replies can be matched against them (semantic
match — Stream 2's validation detector) and the outcome logged as a
validation/correction signal. Without it, the answer is signal lost on the floor.

Same persistence pattern as ``corpus.py``: jsonl, append-only, timestamps are
caller-supplied — this module never invents them. Resolution appends a tombstone
record (``status`` != open) rather than rewriting history; ``list_open`` folds
the log so the latest status per (thread_id, claim_id) wins.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional


class PendingValidations:
    def __init__(self, directory: Path | str):
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.jsonl = self.dir / "validations.jsonl"

    def _append(self, record: dict) -> None:
        with self.jsonl.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def open(self, record: dict) -> None:
        """Record a newly opened validation (status forced to ``open``)."""
        self._append({**record, "status": "open"})

    def resolve(self, thread_id: str, claim_id: str, *, status: str, ts: str,
                note: str = "") -> None:
        """Close an open item by appending a resolution record.

        No-op if the (thread_id, claim_id) has no open record — resolution must
        never invent an item.
        """
        current = {(i["thread_id"], i["claim_id"]): i for i in self._fold().values()}
        item = current.get((thread_id, claim_id))
        if item is None or item.get("status") != "open":
            return
        self._append({
            **item,
            "status": status,
            "resolved_ts": ts,
            "resolution_note": note,
        })

    def _fold(self) -> dict:
        """Latest record per (thread_id, claim_id), in insertion order."""
        folded: dict = {}
        if not self.jsonl.exists():
            return folded
        for ln in self.jsonl.read_text(encoding="utf-8").splitlines():
            if ln.strip():
                rec = json.loads(ln)
                folded[(rec.get("thread_id"), rec.get("claim_id"))] = rec
        return folded

    def list_open(self, thread_id: str) -> list[dict]:
        return [r for r in self._fold().values()
                if r.get("thread_id") == thread_id and r.get("status") == "open"]

    def load(self, status: Optional[str] = None) -> list[dict]:
        """All folded records, optionally filtered by status."""
        out = list(self._fold().values())
        return out if status is None else [r for r in out if r.get("status") == status]
