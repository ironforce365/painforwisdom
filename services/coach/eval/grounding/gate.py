"""Gate orchestration: segment -> judge -> decide -> rewrite -> log -> reassemble.

judge_fn / rewrite_fn are injectable so tests run offline. Defaults use the real
subscription-backed judge and rewriter.
"""
from __future__ import annotations

from typing import Callable, Optional

from .corpus import RegressionCorpus
from .decide import decide
from .judge import judge_claims
from .rewriter import demote_to_question
from .segmenter import segment, strip_claim_tags
from .types import Action, Decision, GateResult, Source
from .validations import PendingValidations


def run_gate(
    draft: str,
    sources: list[Source],
    *,
    temperature: int,
    judge_fn: Callable = judge_claims,
    rewrite_fn: Callable = demote_to_question,
    corpus: Optional[RegressionCorpus] = None,
    validations: Optional[PendingValidations] = None,
    ts: str = "",
    thread_id: str = "",
    user_id: str = "",
) -> GateResult:
    claims, passthrough = segment(draft)
    verdicts = {v.claim_id: v for v in judge_fn(claims, sources)}
    # id -> kind, so decide() can enforce "a fact needs a MEMORY source".
    source_kinds = {s.id: s.kind for s in sources}
    out_lines: list[str] = []
    decisions: list[Decision] = []
    logged: list[str] = []

    for c in claims:
        v = verdicts.get(c.id)
        if v is None:
            # Fail-safe: no verdict -> demote.
            q = rewrite_fn(c, sources)
            out_lines.append(q)
            decisions.append(Decision(c.id, Action.DEMOTE, q))
            continue
        d = decide(v, confidence=c.confidence, temperature=temperature, source_kinds=source_kinds)
        if d.action is Action.ASSERT:
            out_lines.append(c.text)
        elif d.action is Action.STATE_AS_READ:
            out_lines.append(f"My read: {c.text}")
            if validations is not None:
                # a stated read implicitly invites confirmation — remember it so
                # the user's answer can be matched and logged (Stream 2)
                validations.open({
                    "ts": ts,
                    "thread_id": thread_id,
                    "user_id": user_id,
                    "claim_id": c.id,
                    "claim_text": c.text,
                    "claim_type": v.derived_type.value,
                    "confidence": c.confidence,
                    "action": Action.STATE_AS_READ.value,
                    "question": "",
                })
        else:  # DEMOTE
            q = rewrite_fn(c, sources)
            d.question = q
            out_lines.append(q)
            if corpus is not None:
                corpus.append(
                    {
                        "claim_id": c.id,
                        "signal": "catch",
                        "claim_text": c.text,
                        "type": v.derived_type.value,
                        "cited_sources": c.cites,
                        "demoted_question": q,
                        "judge_rationale": v.rationale,
                        "thread_id": thread_id,
                        "user_id": user_id,
                    }
                )
                logged.append(c.id)
            if validations is not None:
                validations.open({
                    "ts": ts,
                    "thread_id": thread_id,
                    "user_id": user_id,
                    "claim_id": c.id,
                    "claim_text": c.text,
                    "claim_type": v.derived_type.value,
                    "confidence": c.confidence,
                    "action": Action.DEMOTE.value,
                    "question": q,
                })
        decisions.append(d)

    if passthrough:
        out_lines.append(passthrough)
    # Last line of defence: never let a tag reach the user, even if one slipped
    # through normalization (malformed marker, unexpected shape). Claim/question
    # text is already clean, so on well-formed output this is a no-op.
    message = strip_claim_tags("\n".join(out_lines))
    return GateResult(message=message, decisions=decisions, logged_ids=logged)
