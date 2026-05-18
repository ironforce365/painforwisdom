"""Pre-validator: shared upstream checks after kb_curator.

Owns checks for transcript + extraction + vault entry + themes/frameworks +
timeline index. Each branch validator only checks artifacts it produced; this
node owns everything before the fan-out so the same finding isn't recomputed
three times.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Dict, List

from pipeline.nodes.validators.shared import check, verdict_from
from pipeline.runtime import VAULT_PATH
from pipeline.state import State

VAULT_ROOT = VAULT_PATH / "gonzalo-book"
EXPECTED_ENTRY_SECTIONS = ("Story Anchor", "Core Insight")


def _run_start_epoch(state: State) -> float:
    rid = str(state.get("run_id", "") or "")
    head = rid.split("_")
    if len(head) < 2:
        return 0.0
    try:
        return time.mktime(time.strptime(f"{head[0]}_{head[1]}", "%Y-%m-%d_%H%M%S"))
    except ValueError:
        return 0.0


def _audit_pre(state: State) -> List[Dict[str, Any]]:
    findings: List[Dict[str, Any]] = []
    run_start = _run_start_epoch(state)

    # Stage 1 — transcript
    tp = state.get("transcript_path", "")
    findings.append(check("transcript exists", bool(tp) and Path(tp).is_file(), tp))
    findings.append(check(
        "transcript word count > 50",
        int(state.get("transcript_word_count", 0)) > 50,
        str(state.get("transcript_word_count", 0)),
        severity="secondary",
    ))

    # Stage 2 — extraction
    erp = state.get("extraction_report_path", "")
    findings.append(check("extraction_report.md exists", bool(erp) and Path(erp).is_file(), erp))
    findings.append(check(
        "Content Quality classified",
        state.get("content_quality", "") in {"Strong", "Weak", "Flagged"},
        str(state.get("content_quality", "")),
        severity="secondary",
    ))

    # Stage 3 — kb-curator vault writes
    vep = state.get("vault_entry_path", "")
    findings.append(check("vault entry exists", bool(vep) and Path(vep).is_file(), vep))
    if vep and Path(vep).is_file():
        body = Path(vep).read_text()
        date = str(state.get("video_date", ""))
        findings.append(check(
            "vault entry date matches video_date",
            date in body,
            f"video_date={date}, file={Path(vep).name}",
            severity="secondary",
        ))
        for section in EXPECTED_ENTRY_SECTIONS:
            findings.append(check(
                f"vault entry has '## {section}' section",
                f"## {section}" in body,
                Path(vep).name,
                severity="secondary",
            ))
    else:
        findings.append(check(
            "vault entry date matches video_date",
            False,
            "no entry to check",
            severity="secondary",
        ))

    for slug in (state.get("themes_attached") or []):
        f = VAULT_ROOT / "themes" / f"{slug}.md"
        findings.append(check(
            f"theme file exists: {slug}",
            f.is_file(),
            str(f),
            severity="secondary",
        ))
        if f.is_file() and run_start and f.stat().st_mtime < run_start:
            findings.append(check(
                f"theme file touched this run: {slug}",
                False,
                f"mtime={f.stat().st_mtime} run_start={run_start}",
                severity="secondary",
            ))
    for slug in (state.get("frameworks_attached") or []):
        f = VAULT_ROOT / "frameworks" / f"{slug}.md"
        findings.append(check(
            f"framework file exists: {slug}",
            f.is_file(),
            str(f),
            severity="secondary",
        ))

    slug = str(state.get("vault_entry_slug", "") or "")
    idx = VAULT_ROOT / "_index.md"
    if idx.is_file() and slug:
        tail = "\n".join(idx.read_text().splitlines()[-10:])
        findings.append(check(
            "timeline _index.md mentions vault entry",
            slug in tail,
            f"slug={slug}",
            severity="secondary",
        ))

    return findings


def node_pre_validator(state: State) -> Dict[str, Any]:
    print("[pre_validator] start")
    findings = _audit_pre(state)
    verdict = verdict_from(findings)
    failed = [f for f in findings if not f["ok"]]
    print(f"[pre_validator] done verdict={verdict} failures={len(failed)}")
    return {"pre_findings": findings, "pre_verdict": verdict}
