"""Rewrite a blocked assertion into a question that surfaces the coach's read
without asserting it as the user's reality (validate-then-analyze)."""
from __future__ import annotations

from eval.llm import call_llm

from .types import Claim, Source

_SYSTEM = (
    "Rewrite the coach's asserted claim as ONE short, open question that surfaces "
    "the coach's read WITHOUT asserting it as the user's reality. Keep the "
    "hypothesis, strip the certainty. Return only the question, nothing else."
)


def demote_to_question(claim: Claim, sources: list[Source], *, model: str = "claude-sonnet-4-6") -> str:
    user = f'CLAIM: "{claim.text}"'
    q = call_llm(system=_SYSTEM, user=user, model=model).strip().strip('"').strip()
    return q if q.endswith("?") else q + "?"
