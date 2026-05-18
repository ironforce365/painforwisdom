"""Branch validator: research lineage (research → notion_research).

Checks artifacts produced by the research branch only. Shared upstream
checks (extraction, vault entry) belong to pre_validator.
"""
from __future__ import annotations

import csv
import io
from pathlib import Path
from typing import Any, Dict, List

from pipeline.nodes.validators.shared import check, verdict_from
from pipeline.state import State


def _audit(state: State) -> List[Dict[str, Any]]:
    findings: List[Dict[str, Any]] = []

    rcp = state.get("research_csv_path", "")
    findings.append(check("research_report.csv exists", bool(rcp) and Path(rcp).is_file(), str(rcp)))

    csv_rows = 0
    if rcp and Path(rcp).is_file():
        try:
            csv_rows = sum(1 for _ in csv.DictReader(io.StringIO(Path(rcp).read_text())))
        except Exception:
            csv_rows = 0
    findings.append(check("research CSV has >=1 verified row", csv_rows >= 1, f"rows={csv_rows}"))

    nrc = int(state.get("notion_task_count", 0))
    findings.append(check(
        "Notion research task count matches CSV rows",
        nrc == csv_rows and csv_rows > 0,
        f"notion={nrc} csv={csv_rows}",
    ))

    return findings


def node_bv_research(state: State) -> Dict[str, Any]:
    print("[bv_research] start")
    findings = _audit(state)
    verdict = verdict_from(findings)
    print(f"[bv_research] done verdict={verdict}")
    return {
        "branch_findings_research": findings,
        "branch_verdict_research": verdict,
        "branch_validations_done": ["research"],
    }
