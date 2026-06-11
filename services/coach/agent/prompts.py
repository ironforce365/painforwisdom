"""System-prompt composition for a coach turn.

Pure file-reading helper kept separate from ``service.py`` so it is importable
and unit-testable on hosts without fastapi/claude_agent_sdk. The claim contract
(``coach_prompt_claims.md``) is appended ONLY when the grounding gate is on —
with the flag off the prompt is byte-identical to today's and no ``[[claim``
tags can ever leak to a user.
"""
from __future__ import annotations

from pathlib import Path

_BASE = Path(__file__).parent.parent / "coach_prompt.md"
_CLAIMS = Path(__file__).parent.parent / "coach_prompt_claims.md"


def compose_system_prompt(*, claims: bool) -> str:
    prompt = _BASE.read_text(encoding="utf-8")
    if claims:
        prompt += "\n\n" + _CLAIMS.read_text(encoding="utf-8")
    return prompt
