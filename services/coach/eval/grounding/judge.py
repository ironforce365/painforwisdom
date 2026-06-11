"""Grounding judge: re-derives each claim's type and checks it against the source
set. One batched LLM call per turn (the seam ``call_llm``, monkeypatched in tests).
"""
from __future__ import annotations

import json

from eval.llm import call_llm

from .types import Claim, ClaimType, Source, Verdict

_SYSTEM = (
    "You are a grounding judge. For each claim, INDEPENDENTLY re-derive its type "
    "(fact = stated as fact about the user; interpretation = the coach's read; "
    "conceptual = general theory). Do NOT trust the coach's own label.\n"
    "Then judge:\n"
    "- fact/conceptual: is it ENTAILED by at least one cited source listed below? "
    "set grounded true/false. An uncited fact/conceptual claim is grounded=false. "
    "An uncited conceptual claim should be re-derived as 'interpretation'.\n"
    "- interpretation: does it CONTRADICT any source? set contradicts true/false.\n"
    'Return ONLY JSON: {"verdicts":[{"claim_id","derived_type","grounded",'
    '"contradicts","rationale"}]} with one entry per claim, in order.'
)


def _fmt_sources(sources: list[Source]) -> str:
    if not sources:
        return "(none)"
    return "\n".join(f"[{s.id}] (tier{s.tier},{s.kind}) {s.text[:400]}" for s in sources)


def _fmt_claims(claims: list[Claim]) -> str:
    return "\n".join(
        f'{c.id} | label={c.type.value} | cites={",".join(c.cites) or "-"} | text="{c.text}"'
        for c in claims
    )


def judge_claims(
    claims: list[Claim], sources: list[Source], *, model: str = "claude-sonnet-4-6"
) -> list[Verdict]:
    if not claims:
        return []
    user = f"SOURCES:\n{_fmt_sources(sources)}\n\nCLAIMS:\n{_fmt_claims(claims)}"
    raw = call_llm(system=_SYSTEM, user=user, model=model)
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError(f"judge returned no JSON object: {raw[:300]}")
    data = json.loads(raw[start : end + 1])
    verdicts: list[Verdict] = []
    for v in data["verdicts"]:
        verdicts.append(
            Verdict(
                claim_id=v["claim_id"],
                derived_type=ClaimType(v["derived_type"]),
                grounded=bool(v.get("grounded", False)),
                contradicts=bool(v.get("contradicts", False)),
                rationale=v.get("rationale", ""),
            )
        )
    return verdicts
