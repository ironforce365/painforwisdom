"""Pure routing decision: hard floor for facts/conceptual, temperature band for
interpretations. No LLM, no I/O -- fully deterministic given a Verdict.

Note: an uncited conceptual claim with no vault support is re-derived as an
interpretation by the judge (see judge._SYSTEM), so by the time it reaches here,
CONCEPTUAL means "genuinely vault-citable theory" and takes the hard floor.
"""
from __future__ import annotations

from typing import Optional

from .types import KIND_DOCTRINE, KIND_MEMORY, Action, ClaimType, Decision, Verdict


def decide(
    verdict: Verdict,
    *,
    confidence: Optional[int],
    temperature: int,
    source_kinds: Optional[dict[str, str]] = None,
) -> Decision:
    """Pure routing. ``source_kinds`` maps source id -> kind.

    TYPED MODE (any source is ``doctrine`` or ``memory``): a FACT about the user
    may be asserted only if a MEMORY source entails it (doctrine can't ground
    biography); a CONCEPTUAL claim may be asserted if any source entails it.
    LEGACY MODE (no typed kinds / no map): trust ``verdict.grounded`` as before.
    """
    dt = verdict.derived_type
    typed = bool(source_kinds) and any(
        k in (KIND_DOCTRINE, KIND_MEMORY) for k in source_kinds.values()
    )
    if dt is ClaimType.FACT:
        if typed:
            grounded = any(
                source_kinds.get(sid) == KIND_MEMORY for sid in verdict.grounded_by
            )
        else:
            grounded = verdict.grounded
        # HARD FLOOR -- temperature ignored.
        action = Action.ASSERT if grounded else Action.DEMOTE
        return Decision(claim_id=verdict.claim_id, action=action)
    if dt is ClaimType.CONCEPTUAL:
        if typed:
            grounded = bool(verdict.grounded_by)
        else:
            grounded = verdict.grounded
        # HARD FLOOR -- temperature ignored.
        action = Action.ASSERT if grounded else Action.DEMOTE
        return Decision(claim_id=verdict.claim_id, action=action)
    # INTERPRETATION -- temperature band.
    if verdict.contradicts:
        return Decision(claim_id=verdict.claim_id, action=Action.DEMOTE)
    conf = confidence if confidence is not None else 1  # missing -> least confident
    action = Action.STATE_AS_READ if conf >= temperature else Action.DEMOTE
    return Decision(claim_id=verdict.claim_id, action=action)
