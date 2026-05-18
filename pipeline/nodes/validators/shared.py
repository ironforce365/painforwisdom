"""Shared validator helpers: Finding shape, check builder, verdict aggregator."""
from __future__ import annotations

from typing import Any, Dict, List


def check(name: str, ok: bool, detail: str = "", severity: str = "core") -> Dict[str, Any]:
    """Build a Finding dict.

    severity ∈ {"core", "secondary"}. core failures → FAIL verdict.
    secondary failures → PARTIAL verdict.
    """
    return {"name": name, "ok": bool(ok), "detail": str(detail), "severity": severity}


def verdict_from(findings: List[Dict[str, Any]]) -> str:
    """Aggregate verdict across findings.

    PASS = all ok. PARTIAL = only secondary failures. FAIL = any core failure.
    """
    core_failed = [f for f in findings if not f["ok"] and f["severity"] == "core"]
    if core_failed:
        return "FAIL"
    secondary_failed = [f for f in findings if not f["ok"] and f["severity"] == "secondary"]
    if secondary_failed:
        return "PARTIAL"
    return "PASS"
