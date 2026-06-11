"""Load synthetic self-labeling fixtures (planted-content debriefs).

Each fixture: {id, note?, sources:[{id,tier,kind,text}], draft, expect:{assert,
demote, state_as_read}}. The planted expectations are the ground truth, so the
harness needs no human labels for these.
"""
from __future__ import annotations

import json
from pathlib import Path

_DIR = Path(__file__).parent / "cases"


def load_fixtures() -> list[dict]:
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(_DIR.glob("*.json"))]
