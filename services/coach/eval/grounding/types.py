"""Core types for the grounding gate."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


# Source kinds that carry epistemic ROLE (Stream 3 — doctrine vs. memory).
# A fact about the user may only be grounded in a MEMORY source (what the user
# actually said, conversation-derived); a DOCTRINE source (distilled, de-
# personalised vault wisdom) may only ground conceptual/principle claims. Other
# kind strings (debrief, vault_entry, …) are "untyped" and keep legacy behaviour.
KIND_DOCTRINE = "doctrine"
KIND_MEMORY = "memory"


class ClaimType(str, Enum):
    FACT = "fact"                    # stated as fact about the user
    INTERPRETATION = "interpretation"  # the coach's read, beyond the literal source
    CONCEPTUAL = "conceptual"        # general/theoretical, not user-specific


class Action(str, Enum):
    ASSERT = "assert"                # send as-is
    STATE_AS_READ = "state_as_read"  # interpretation above temperature -> "My read: ..."
    DEMOTE = "demote"                # rewrite to a question


@dataclass
class Source:
    id: str
    tier: int            # 1 (primary truth) or 2 (coach-derived)
    kind: str            # thread|debrief|vault_entry|vault_framework|memory
    text: str


@dataclass
class Claim:
    id: str
    type: ClaimType
    text: str
    cites: list[str] = field(default_factory=list)
    confidence: Optional[int] = None  # 1-10, for interpretations


@dataclass
class Verdict:
    claim_id: str
    derived_type: ClaimType  # judge re-derived (anti-dodge), may differ from Claim.type
    grounded: bool           # fact/conceptual: cited & entailed by ≥1 cited source
    contradicts: bool        # interpretation: contradicts a source
    rationale: str
    # Source ids that ENTAIL the claim (subset of cites). Lets decide() apply the
    # source-typed rule: a fact must be entailed by a MEMORY source, not doctrine.
    # Empty when the judge predates source typing → decide() falls back to legacy.
    grounded_by: list[str] = field(default_factory=list)


@dataclass
class Decision:
    claim_id: str
    action: Action
    question: Optional[str] = None  # filled when action == DEMOTE


@dataclass
class GateResult:
    message: str             # reassembled, gated message
    decisions: list[Decision]
    logged_ids: list[str]    # claim ids written to the regression corpus
