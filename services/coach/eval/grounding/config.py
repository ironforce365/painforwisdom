"""Per-user grounding knobs.

- temperature: governs interpretation validation (demote iff confidence < temperature).
- absorption: governs how readily validated claims promote into grounding.

Both 1-10. Defaults are neutral until calibration sets the real values
(spec sec 2.5 / 14).
"""
from __future__ import annotations

import os
from dataclasses import dataclass

_DEFAULT = 5


def _read(name: str) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return _DEFAULT
    try:
        return max(1, min(10, int(raw)))
    except ValueError:
        return _DEFAULT


@dataclass
class GroundingConfig:
    temperature: int = _DEFAULT
    absorption: int = _DEFAULT


def load_config() -> GroundingConfig:
    return GroundingConfig(
        temperature=_read("COACH_GROUNDING_TEMPERATURE"),
        absorption=_read("COACH_GROUNDING_ABSORPTION"),
    )
