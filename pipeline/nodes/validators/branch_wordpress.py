"""Branch validator: blog lineage (writer → notion_blog → wordpress_draft).

Also owns featured image check (extract_image joins into wordpress_draft).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from pipeline.nodes.validators.shared import check, verdict_from
from pipeline.state import State


def fetch_page_blocks(page_id_str: str) -> List[Dict[str, Any]]:
    """Thin wrapper around pipeline.notion_client.fetch_page_blocks.

    Defined here so tests can patch it at the import site
    (pipeline.nodes.validators.branch_wordpress.fetch_page_blocks)
    without triggering the notion-client SDK import at module load time.
    """
    from pipeline.notion_client import fetch_page_blocks as _real  # noqa: PLC0415
    return _real(page_id_str)


def _audit(state: State) -> List[Dict[str, Any]]:
    findings: List[Dict[str, Any]] = []

    bp = state.get("blog_post_path", "")
    findings.append(check("blog_post.md exists", bool(bp) and Path(bp).is_file(), str(bp)))

    bu = state.get("notion_blog_url", "")
    findings.append(check("Notion blog page URL recorded", bool(bu), str(bu)))
    if bu:
        try:
            page_id = str(bu).rstrip("/").split("-")[-1].replace(":", "").replace("/", "")
            blocks = fetch_page_blocks(page_id)
            findings.append(check("Notion blog body non-empty", len(blocks) > 0, f"blocks={len(blocks)}"))
        except Exception as exc:
            findings.append(check(
                "Notion blog body non-empty",
                False,
                f"fetch error: {exc}",
                severity="secondary",
            ))

    # WordPress draft: PASS if url present OR dormant OR skipped-with-reason.
    if state.get("wordpress_url"):
        findings.append(check("WordPress draft created or skipped cleanly", True, str(state["wordpress_url"])))
    elif state.get("wordpress_dormant"):
        findings.append(check("WordPress draft created or skipped cleanly", True, "dormant"))
    elif state.get("wordpress_skipped"):
        findings.append(check(
            "WordPress draft created or skipped cleanly",
            True,
            f"skipped: {state.get('wordpress_skip_reason', '')}",
            severity="secondary",
        ))
    else:
        findings.append(check(
            "WordPress draft created or skipped cleanly",
            False,
            "no url, not dormant, not skipped",
            severity="secondary",
        ))

    # Featured image: present OR explicitly failed/skipped flag.
    fip = state.get("featured_image_path", "")
    if fip:
        findings.append(check("featured image present or explicitly skipped", True, fip, severity="secondary"))
    elif state.get("image_extraction_failed"):
        findings.append(check("featured image present or explicitly skipped", True, "skipped", severity="secondary"))
    else:
        findings.append(check(
            "featured image present or explicitly skipped",
            False,
            "no image, no skip flag",
            severity="secondary",
        ))

    return findings


def node_bv_wordpress(state: State) -> Dict[str, Any]:
    print("[bv_wordpress] start")
    findings = _audit(state)
    verdict = verdict_from(findings)
    print(f"[bv_wordpress] done verdict={verdict}")
    return {
        "branch_findings_wordpress": findings,
        "branch_verdict_wordpress": verdict,
        "branch_validations_done": ["wordpress"],
    }
