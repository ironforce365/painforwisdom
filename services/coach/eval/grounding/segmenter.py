"""Parse a tagged coach draft into claims (citation-first: parse, don't discover).

Tag format, one claim per line:
    [[claim id=c1 type=fact cite=S1,S2]] free text of the claim
    [[claim id=c2 type=interpretation conf=7]] the coach's read
Untagged non-empty lines are returned as passthrough (greetings, questions).
"""
from __future__ import annotations

import re

from .types import Claim, ClaimType

_TAG = re.compile(r"^\s*\[\[claim\s+([^\]]*)\]\]\s*(.*)$")


def _attrs(blob: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for tok in blob.split():
        if "=" in tok:
            k, v = tok.split("=", 1)
            out[k] = v
    return out


def segment(draft: str) -> tuple[list[Claim], str]:
    claims: list[Claim] = []
    passthrough: list[str] = []
    for line in draft.splitlines():
        m = _TAG.match(line)
        if not m:
            if line.strip():
                passthrough.append(line)
            continue
        a = _attrs(m.group(1))
        cites = [c for c in a.get("cite", "").split(",") if c]
        conf = int(a["conf"]) if a.get("conf", "").isdigit() else None
        claims.append(
            Claim(
                id=a.get("id", f"auto{len(claims)}"),
                type=ClaimType(a.get("type", "interpretation")),
                text=m.group(2).strip(),
                cites=cites,
                confidence=conf,
            )
        )
    return claims, "\n".join(passthrough)
