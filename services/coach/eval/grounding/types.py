"""Core types for the grounding gate."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


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
    grounded: bool           # fact/conceptual: cited & entailed by a Tier-1 source
    contradicts: bool        # interpretation: contradicts a source
    rationale: str


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
