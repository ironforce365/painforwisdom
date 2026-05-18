"""Branch validator: youtube_upload branch.

YouTube is optional (gated by ``YOUTUBE_ENABLED``). PASS = url present or
skipped cleanly. Silent absence = secondary failure (PARTIAL), never core.
"""
from __future__ import annotations

from typing import Any, Dict, List

from pipeline.nodes.validators.shared import check, verdict_from
from pipeline.state import State


def _audit(state: State) -> List[Dict[str, Any]]:
    findings: List[Dict[str, Any]] = []

    yt_url = state.get("youtube_url", "")
    if yt_url:
        findings.append(check("YouTube upload produced URL or skipped cleanly", True, yt_url, severity="secondary"))
    elif state.get("youtube_skipped"):
        reason = state.get("youtube_skip_reason", "skipped")
        findings.append(check(
            "YouTube upload produced URL or skipped cleanly",
            True,
            f"skipped: {reason}",
            severity="secondary",
        ))
    else:
        findings.append(check(
            "YouTube upload produced URL or skipped cleanly",
            False,
            "no url, no skip flag",
            severity="secondary",
        ))

    return findings


def node_bv_youtube(state: State) -> Dict[str, Any]:
    print("[bv_youtube] start")
    findings = _audit(state)
    verdict = verdict_from(findings)
    print(f"[bv_youtube] done verdict={verdict}")
    return {
        "branch_findings_youtube": findings,
        "branch_verdict_youtube": verdict,
        "branch_validations_done": ["youtube"],
    }
