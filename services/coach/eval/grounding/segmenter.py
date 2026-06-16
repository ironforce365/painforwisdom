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
# Non-anchored variant for the gate's fail-safe: strip a claim marker wherever it
# appears (even mid-line, even if the coach violated the one-per-line contract),
# keeping the surrounding text. Last line of defence before a reply reaches the user.
_TAG_ANYWHERE = re.compile(r"\[\[claim\s+[^\]]*\]\]\s*")
# Opening of a claim marker, anywhere. Used to push a mid-line tag onto its own
# line before parsing so the line-anchored matcher above can lift it.
_TAG_ANYWHERE_PREFIX = re.compile(r"\[\[claim\b")


def strip_claim_tags(draft: str) -> str:
    """Remove every ``[[claim ...]]`` marker, preserving the claim text and order.

    Used on the gate's exception path AND as the gate's final belt-and-suspenders
    pass: if the judge errors, or the coach buries a tag mid-line where the
    parser can't lift it into a judged claim, we must still never leak the
    internal protocol tags to the user. On tag-free text (flag off) this is a
    no-op.
    """
    return _TAG_ANYWHERE.sub("", draft)


def _normalize_inline_tags(draft: str) -> str:
    """Force every ``[[claim ...]]`` marker onto its own line.

    The one-per-line contract is the coach's responsibility, but it sometimes
    violates it — burying a tag mid-sentence (``...the price. [[claim ...]]
    You're aware``). The line-anchored parser would then drop the whole line to
    passthrough, leaking the tag AND skipping the grounding check for that claim.
    Splitting at each marker turns a mid-line tag into a line-anchored one, so it
    is parsed and judged like any other; the text before it becomes its own
    passthrough line. Blank lines this introduces are ignored by ``segment``.
    """
    return _TAG_ANYWHERE_PREFIX.sub(r"\n\g<0>", draft)


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
    for line in _normalize_inline_tags(draft).splitlines():
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
