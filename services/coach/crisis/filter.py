"""Pre-Claude keyword interception. If hit, agent never sees the turn."""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import yaml

_HERE = Path(__file__).parent

with (_HERE / "keywords.yaml").open() as f:
    _TRIGGERS: list[str] = [t.lower() for t in yaml.safe_load(f)["triggers"]]

_CANNED_REPLY: str = (_HERE / "canned_reply.md").read_text(encoding="utf-8")


@dataclass(frozen=True)
class CrisisHit:
    trigger: str
    canned_reply: str


def check_message(text: str) -> CrisisHit | None:
    lowered = text.lower()
    for trig in _TRIGGERS:
        if trig in lowered:
            return CrisisHit(trigger=trig, canned_reply=_CANNED_REPLY)
    return None
