"""Summarizer: joins all three branch validators + pre-validator into one report.

LangGraph fires this node once per parent completion (3 fires per run because
there are 3 inbound edges, one per branch validator). The first 2 fires are
no-ops: they return ``{}`` without writing files or sending Telegram. The 3rd
fire (when ``len(branch_validations_done) == 3``) aggregates everything, writes
``audit_report.md`` + ``pipeline_summary.md``, and sends a single Telegram
message.

This idempotency is intentional: we cannot turn LangGraph's "fire-per-parent"
semantics off, so we make repeated fires safe instead.
"""
from __future__ import annotations

import csv
import io
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple

from pipeline.runtime import append_metric, run_telemetry_path
from pipeline.state import State
from pipeline.telegram import send as telegram_send

EXPECTED_BRANCHES = {"research", "wordpress", "youtube"}


def _aggregate_verdict(state: State) -> Tuple[str, List[str]]:
    """Return (aggregate_verdict, [reasons])."""
    verdicts = [
        state.get("pre_verdict", "PASS"),
        state.get("branch_verdict_research", "PASS"),
        state.get("branch_verdict_wordpress", "PASS"),
        state.get("branch_verdict_youtube", "PASS"),
    ]
    if "FAIL" in verdicts:
        return "FAIL", verdicts
    if "PARTIAL" in verdicts:
        return "PARTIAL", verdicts
    return "PASS", verdicts


def _all_findings(state: State) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    out.extend(state.get("pre_findings", []) or [])
    out.extend(state.get("branch_findings_research", []) or [])
    out.extend(state.get("branch_findings_wordpress", []) or [])
    out.extend(state.get("branch_findings_youtube", []) or [])
    return out


def _wp_line(state: State) -> str:
    if state.get("wordpress_url"):
        return f"Stage 7 — wordpress draft:  ✓ {state.get('wordpress_url', '')}"
    if state.get("wordpress_dormant"):
        return "Stage 7 — wordpress draft:  ⏸ dormant (WORDPRESS_ENABLED!=true; bundle on disk)"
    if state.get("wordpress_skipped"):
        reason = state.get("wordpress_skip_reason", "skipped")
        return f"Stage 7 — wordpress draft:  ⏭ skipped ({reason})"
    return "Stage 7 — wordpress draft:  — (not run)"


def _yt_line(state: State) -> str:
    if state.get("youtube_url"):
        return f"Stage 8 — youtube upload:   ✓ {state.get('youtube_url', '')}"
    if state.get("youtube_skipped"):
        reason = state.get("youtube_skip_reason", "skipped")
        return f"Stage 8 — youtube upload:   ⏭ skipped ({reason})"
    return "Stage 8 — youtube upload:   — (not run)"


def _image_line(state: State) -> str:
    fip = state.get("featured_image_path", "")
    if fip:
        score = state.get("featured_image_score", 0.0) or 0.0
        return f"Featured image:              ✓ {fip} (score={score:.1f})"
    if state.get("image_extraction_failed"):
        return "Featured image:              ⏭ failed/skipped"
    return "Featured image:              — (none)"


def _render_pipeline_summary(state: State, verdict: str) -> str:
    quality = state.get("content_quality", "?")
    csv_rows = "?"
    rcp = state.get("research_csv_path", "")
    if rcp and Path(rcp).is_file():
        try:
            csv_rows = sum(1 for _ in csv.DictReader(io.StringIO(Path(rcp).read_text())))
        except Exception:
            pass
    icon = {"PASS": "🎉", "PARTIAL": "⚠️", "FAIL": "❌"}.get(verdict, "❓")
    lines = [
        f"{icon} Pipeline {verdict} — {Path(state.get('transcript_path','')).stem}",
        f"RUN ID: {state.get('run_id','')}",
        "",
        f"Stage 1 — extraction:        ✓ extraction_report.md — {quality}",
        f"Stage 2 — kb-curator:        ✓ entry [[{state.get('vault_entry_slug','')}]]",
        f"Stage 3 — blog writer:       ✓ blog_post.md ({state.get('blog_post_title','?')!r})",
        f"Stage 4 — notion blog post:  ✓ {state.get('notion_blog_url','-')}",
        f"Stage 5 — research:          ✓ research_report.csv ({csv_rows} refs)",
        f"Stage 6 — notion research:   ✓ {state.get('notion_task_count', 0)} tasks",
        _wp_line(state),
        _yt_line(state),
        _image_line(state),
    ]
    return "\n".join(lines)


def _render_audit_md(state: State, findings: List[Dict[str, Any]], verdict: str) -> str:
    lines = [
        "# Pipeline Audit Report",
        "",
        f"**Run ID:** {state.get('run_id','')}",
        f"**Video date:** {state.get('video_date','')}",
        f"**Run dir:** {state.get('run_dir','')}",
        f"**Verdict:** {verdict}",
        "",
        f"**Pre-validator verdict:** {state.get('pre_verdict','?')}",
        f"**Research branch verdict:** {state.get('branch_verdict_research','?')}",
        f"**WordPress branch verdict:** {state.get('branch_verdict_wordpress','?')}",
        f"**YouTube branch verdict:** {state.get('branch_verdict_youtube','?')}",
        "",
        "## Findings",
        "",
        "| Check | Severity | Result | Detail |",
        "|---|---|---|---|",
    ]
    for f in findings:
        status = "✓ PASS" if f["ok"] else "✗ FAIL"
        lines.append(f"| {f['name']} | {f['severity']} | {status} | {f['detail']} |")
    return "\n".join(lines) + "\n"


def _send_summary_with_retry(text: str, attempts: int = 2) -> Tuple[bool, str]:
    last_err = ""
    for i in range(attempts):
        try:
            rc = telegram_send(text)
            if rc == 0:
                return True, ""
            last_err = f"rc={rc}"
        except Exception as exc:  # noqa: BLE001
            last_err = f"{type(exc).__name__}: {exc}"
        if i + 1 < attempts:
            time.sleep(2.0)
    return False, last_err


def node_summarizer(state: State) -> Dict[str, Any]:
    done = set(state.get("branch_validations_done", []) or [])
    if done != EXPECTED_BRANCHES:
        # Earlier-than-final fire: no-op until all three branch validators have
        # reported. Returning {} leaves state untouched.
        print(f"[summarizer] skip (done={sorted(done)} expected={sorted(EXPECTED_BRANCHES)})")
        return {}

    t0 = time.time()
    print("[summarizer] start (all branches done)")

    verdict, _verdicts = _aggregate_verdict(state)
    findings = _all_findings(state)

    run_dir = Path(state["run_dir"])
    audit_dir = run_dir / "validator"
    audit_dir.mkdir(parents=True, exist_ok=True)
    summary_dir = run_dir / "pipeline-summary"
    summary_dir.mkdir(parents=True, exist_ok=True)

    summary_text = _render_pipeline_summary(state, verdict)
    failed = [f for f in findings if not f["ok"]]
    if failed:
        summary_text += "\n\n⚠️ Audit findings:\n" + "\n".join(
            f"  ✗ {f['name']} — {f['detail']}" for f in failed
        )

    delivered, send_err = _send_summary_with_retry(summary_text)
    # Record Telegram delivery as a synthetic secondary finding so the audit
    # report reflects send failures even after aggregation.
    findings = list(findings) + [{
        "name": "Telegram summary delivered",
        "ok": delivered,
        "detail": "" if delivered else send_err,
        "severity": "secondary",
    }]
    if not delivered and verdict == "PASS":
        verdict = "PARTIAL"

    audit_path = audit_dir / "audit_report.md"
    audit_path.write_text(_render_audit_md(state, findings, verdict))
    summary_path = summary_dir / "pipeline_summary.md"
    summary_path.write_text(_render_pipeline_summary(state, verdict))

    duration = time.time() - t0
    append_metric(
        run_telemetry_path(state["run_dir"]),
        "summarizer",
        duration_s=round(duration, 2),
        verdict=verdict,
        checks=len(findings),
        failures=len([f for f in findings if not f["ok"]]),
        telegram_delivered=delivered,
    )
    print(
        f"[summarizer] done {duration:.1f}s verdict={verdict} "
        f"telegram={'ok' if delivered else 'failed'}"
    )
    return {
        "validator_verdict": verdict,
        "validator_report_path": str(audit_path),
        "pipeline_summary_path": str(summary_path),
    }
