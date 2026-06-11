"""Pure routing decision: hard floor for facts/conceptual, temperature band for
interpretations. No LLM, no I/O -- fully deterministic given a Verdict.

Note: an uncited conceptual claim with no vault support is re-derived as an
interpretation by the judge (see judge._SYSTEM), so by the time it reaches here,
CONCEPTUAL means "genuinely vault-citable theory" and takes the hard floor.
"""
from __future__ import annotations

from typing import Optional

from .types import Action, ClaimType, Decision, Verdict


def decide(verdict: Verdict, *, confidence: Optional[int], temperature: int) -> Decision:
    dt = verdict.derived_type
    if dt in (ClaimType.FACT, ClaimType.CONCEPTUAL):
        # HARD FLOOR -- temperature ignored.
        action = Action.ASSERT if verdict.grounded else Action.DEMOTE
        return Decision(claim_id=verdict.claim_id, action=action)
    # INTERPRETATION -- temperature band.
    if verdict.contradicts:
        return Decision(claim_id=verdict.claim_id, action=Action.DEMOTE)
    conf = confidence if confidence is not None else 1  # missing -> least confident
    action = Action.STATE_AS_READ if conf >= temperature else Action.DEMOTE
    return Decision(claim_id=verdict.claim_id, action=action)
