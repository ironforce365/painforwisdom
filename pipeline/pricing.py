"""API-equivalent cost from the one price table, config/pricing.json (spec 2026-11-05-console-end-to-end section 3).

Standard library only: the UI server imports it. An unknown model is None, never a fake 0.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

CATALOG = Path(__file__).resolve().parent.parent / "config" / "pricing.json"
PRICES: dict[str, dict] = json.loads(CATALOG.read_text())["models"]
_MTOK = 1_000_000


def cost(model: Optional[str], input: int, output: int, cache_read: int, cache_write: int) -> Optional[float]:
    """USD for these non-overlapping token counts; None when the model has no price."""
    price = PRICES.get(str(model).removeprefix("anthropic/"))
    if price is None:
        return None
    return (input * price["input"] + output * price["output"] + cache_read * price["cache_read"]
            + cache_write * price["cache_write"]) / _MTOK
