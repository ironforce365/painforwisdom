"""Stage 6 — validator + run summary.

Pure-Python audit (no LLM). Cross-stage assertions split into:

- **core** checks: a failure here means a primary artifact is missing or
  unusable (no vault entry, no blog file, no Notion blog page, no research
  CSV rows, etc.). Verdict = ``FAIL``. Batch orchestrator quarantines the
  video.
- **secondary** checks: structural / cosmetic deviations that do not
  invalidate the run (vault entry section missing, theme file not touched,
  Telegram send failed). Verdict = ``PARTIAL``. Run is still moved to
  ``processed/`` but the user is told what was off.

Verdict states:

- ``PASS``    — all checks ok, Telegram delivered.
- ``PARTIAL`` — data ok, at least one secondary check failed.
- ``FAIL``    — at least one core check failed.

Writes ``audit_report.md``, ``pipeline_summary.md``, and sends a Telegram
summary. Telegram delivery is now retried once on failure and surfaced as
a secondary finding rather than swallowed silently.
"""
from __future__ import annotations

import csv
import io
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple

from pipeline.notion_client import fetch_page_blocks
from pipeline.runtime import VAULT_PATH, append_metric, run_telemetry_path
from pipeline.state import State
from pipeline.telegram import send as telegram_send


# Each finding is (name, ok, detail, severity). severity ∈ {"core","secondary"}.
Finding = Tuple[str, bool, str, str]

VAULT_ROOT = VAULT_PATH / "gonzalo-book"
EXPECTED_ENTRY_SECTIONS = ("Story Anchor", "Core Insight")


def _check(name: str, ok: bool, detail: str = "", severity: str = "core") -> Finding:
    return (name, ok, detail, severity)


def _run_start_epoch(state: State) -> float:
    """Best-effort: parse run_id timestamp prefix back to epoch seconds.
    run_id format is YYYY-MM-DD_HHMMSS or YYYY-MM-DD_HHMMSS_NNN.
    Returns 0.0 on parse failure (i.e. all mtime checks become permissive)."""
    rid = str(state.get("run_id", "") or "")
    head = rid.split("_")
    if len(head) < 2:
        return 0.0
    try:
        return time.mktime(time.strptime(f"{head[0]}_{head[1]}", "%Y-%m-%d_%H%M%S"))
    except ValueError:
        return 0.0


def _audit(state: State) -> List[Finding]:
    findings: List[Finding] = []
    run_start = _run_start_epoch(state)

    # Stage 1 — transcript
    tp = state.get("transcript_path", "")
    findings.append(_check("transcript exists", bool(tp) and Path(tp).is_file(), tp))
    findings.append(
        _check(
            "transcript word count > 50",
            int(state.get("transcript_word_count", 0)) > 50,
            str(state.get("transcript_word_count", 0)),
            severity="secondary",
        )
    )

    # Stage 2 — extraction
    erp = state.get("extraction_report_path", "")
    findings.append(_check("extraction_report.md exists", bool(erp) and Path(erp).is_file(), erp))
    findings.append(
        _check(
            "Content Quality classified",
            state.get("content_quality", "") in {"Strong", "Weak", "Flagged"},
            str(state.get("content_quality", "")),
            severity="secondary",
        )
    )

    # Stage 3 — kb-curator vault writes
    vep = state.get("vault_entry_path", "")
    findings.append(_check("vault entry exists", bool(vep) and Path(vep).is_file(), vep))
    if vep and Path(vep).is_file():
        body = Path(vep).read_text()
        date = str(state.get("video_date", ""))
        findings.append(
            _check(
                "vault entry date matches video_date",
                date in body,
                f"video_date={date}, file={Path(vep).name}",
                severity="secondary",
            )
        )
        for section in EXPECTED_ENTRY_SECTIONS:
            findings.append(
                _check(
                    f"vault entry has '## {section}' section",
                    f"## {section}" in body,
                    Path(vep).name,
                    severity="secondary",
                )
            )
    else:
        findings.append(
            _check(
                "vault entry date matches video_date",
                False,
                "no entry to check",
                severity="secondary",
            )
        )

    # Theme + framework files referenced by the run should exist + have been
    # written during this run (mtime >= run_start). Soft check: if the model
    # mapped the entry only to existing themes with no body changes, files
    # may not have been touched; accept that — only fail when the file is
    # missing entirely.
    # Validate against kb-curator's authoritative themes_attached/frameworks_attached
    # (the slugs actually wired into the entry), not extraction's raw themes
    # list (which may include slugs the curator chose not to attach).
    for slug in (state.get("themes_attached") or []):
        f = VAULT_ROOT / "themes" / f"{slug}.md"
        findings.append(
            _check(
                f"theme file exists: {slug}",
                f.is_file(),
                str(f),
                severity="secondary",
            )
        )
        if f.is_file() and run_start and f.stat().st_mtime < run_start:
            findings.append(
                _check(
                    f"theme file touched this run: {slug}",
                    False,
                    f"mtime={f.stat().st_mtime} run_start={run_start}",
                    severity="secondary",
                )
            )
    for slug in (state.get("frameworks_attached") or []):
        f = VAULT_ROOT / "frameworks" / f"{slug}.md"
        findings.append(
            _check(
                f"framework file exists: {slug}",
                f.is_file(),
                str(f),
                severity="secondary",
            )
        )

    # Timeline _index.md should mention the new entry slug somewhere in the
    # tail (kb-curator appends a row).
    slug = str(state.get("vault_entry_slug", "") or "")
    idx = VAULT_ROOT / "_index.md"
    if idx.is_file() and slug:
        tail = "\n".join(idx.read_text().splitlines()[-10:])
        findings.append(
            _check(
                "timeline _index.md mentions vault entry",
                slug in tail,
                f"slug={slug}",
                severity="secondary",
            )
        )

    # Stage 4a — blog file
    bp = state.get("blog_post_path", "")
    findings.append(_check("blog_post.md exists", bool(bp) and Path(bp).is_file(), str(bp)))

    # Stage 4b — research CSV
    rcp = state.get("research_csv_path", "")
    findings.append(_check("research_report.csv exists", bool(rcp) and Path(rcp).is_file(), str(rcp)))
    csv_rows = 0
    if rcp and Path(rcp).is_file():
        try:
            csv_rows = sum(1 for _ in csv.DictReader(io.StringIO(Path(rcp).read_text())))
        except Exception:
            csv_rows = 0
    findings.append(_check("research CSV has >=1 verified row", csv_rows >= 1, f"rows={csv_rows}"))

    # Stage 5a — Notion blog
    bu = state.get("notion_blog_url", "")
    findings.append(_check("Notion blog page URL recorded", bool(bu), str(bu)))
    if bu:
        try:
            page_id = str(bu).rstrip("/").split("-")[-1].replace(":", "").replace("/", "")
            blocks = fetch_page_blocks(page_id)
            findings.append(_check("Notion blog body non-empty", len(blocks) > 0, f"blocks={len(blocks)}"))
        except Exception as exc:
            findings.append(
                _check(
                    "Notion blog body non-empty",
                    False,
                    f"fetch error: {exc}",
                    severity="secondary",
                )
            )

    # Stage 5b — Notion research tasks count
    nrc = int(state.get("notion_task_count", 0))
    findings.append(
        _check(
            "Notion research task count matches CSV rows",
            nrc == csv_rows and csv_rows > 0,
            f"notion={nrc} csv={csv_rows}",
        )
    )

    return findings


def _verdict(findings: List[Finding]) -> str:
    core_failed = [f for f in findings if not f[1] and f[3] == "core"]
    if core_failed:
        return "FAIL"
    secondary_failed = [f for f in findings if not f[1] and f[3] == "secondary"]
    if secondary_failed:
        return "PARTIAL"
    return "PASS"


def _render_audit_md(state: State, findings: List[Finding], verdict: str) -> str:
    lines = [
        "# Pipeline Audit Report",
        "",
        f"**Run ID:** {state.get('run_id','')}",
        f"**Video date:** {state.get('video_date','')}",
        f"**Run dir:** {state.get('run_dir','')}",
        f"**Verdict:** {verdict}",
        "",
        "## Findings",
        "",
        "| Check | Severity | Result | Detail |",
        "|---|---|---|---|",
    ]
    for name, ok, detail, sev in findings:
        status = "✓ PASS" if ok else "✗ FAIL"
        lines.append(f"| {name} | {sev} | {status} | {detail} |")
    return "\n".join(lines) + "\n"


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
    ]
    return "\n".join(lines)


def _send_summary_with_retry(text: str, attempts: int = 2) -> Tuple[bool, str]:
    """Returns (delivered, last_error_string). Retries once on non-zero rc."""
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


def node_validator(state: State) -> Dict[str, Any]:
    t0 = time.time()
    print("[validator] start")

    findings = _audit(state)

    run_dir = Path(state["run_dir"])
    audit_dir = run_dir / "validator"
    audit_dir.mkdir(parents=True, exist_ok=True)

    summary_dir = run_dir / "pipeline-summary"
    summary_dir.mkdir(parents=True, exist_ok=True)

    # Telegram summary first so we can record its outcome as a finding before
    # writing the audit / summary files.
    pre_verdict = _verdict(findings)
    summary_text = _render_pipeline_summary(state, pre_verdict)
    failed_pre = [f for f in findings if not f[1]]
    if failed_pre:
        summary_text += "\n\n⚠️ Audit findings:\n" + "\n".join(
            f"  ✗ {name} — {detail}" for name, _, detail, _ in failed_pre
        )

    delivered, send_err = _send_summary_with_retry(summary_text)
    findings.append(
        _check(
            "Telegram summary delivered",
            delivered,
            send_err if not delivered else "",
            severity="secondary",
        )
    )

    verdict = _verdict(findings)

    audit_path = audit_dir / "audit_report.md"
    audit_path.write_text(_render_audit_md(state, findings, verdict))
    summary_path = summary_dir / "pipeline_summary.md"
    summary_path.write_text(_render_pipeline_summary(state, verdict))

    duration = time.time() - t0
    failed = [f for f in findings if not f[1]]
    append_metric(
        run_telemetry_path(state["run_dir"]),
        "validator",
        duration_s=round(duration, 2),
        verdict=verdict,
        checks=len(findings),
        failures=len(failed),
        telegram_delivered=delivered,
    )
    print(
        f"[validator] done {duration:.1f}s verdict={verdict} "
        f"failures={len(failed)} telegram={'ok' if delivered else 'failed'}"
    )
    return {
        "validator_verdict": verdict,
        "validator_report_path": str(audit_path),
        "pipeline_summary_path": str(summary_path),
    }
