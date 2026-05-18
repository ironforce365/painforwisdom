# Per-Branch Validators + Summarizer Join Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the single fan-in `validator` node (which fires once per terminal-branch completion, sending 3 Telegram messages with stale snapshots) with one shared pre-validator + three branch validators + a summarizer join, producing exactly one final Telegram message with accurate per-branch verdicts.

**Architecture:**
- `pre_validator` runs once after `kb_curator`. Owns extraction + vault checks. Writes `pre_findings` to state.
- Three branch validators (`bv_research`, `bv_wordpress`, `bv_youtube`) each sit at the end of their respective branches. Each owns checks for its branch's artifacts only and writes `branch_findings_<name>` + `branch_verdict_<name>` to state. Each appends its name to a reducer-backed `branch_validations_done` list.
- `summarizer` has three inbound edges (one per branch validator). LangGraph fires it once per parent completion (3 fires per run). On each fire it inspects `len(branch_validations_done)`; only the final fire (== 3) renders the aggregated summary, writes the audit + summary files, and sends the single Telegram message.
- A branch failing in node execution prevents only that branch from contributing — the other two branches still produce verdicts. If a branch never reports (e.g. exception), summarizer never reaches 3 and orchestrator notices via missing `validator_verdict`.

**Tech Stack:** Python 3.12, LangGraph (`StateGraph`, reducers via `Annotated[List, add]`), `unittest`.

---

## File Structure

**New files:**
- `pipeline/nodes/validators/__init__.py` — package marker, re-exports public nodes
- `pipeline/nodes/validators/shared.py` — `Finding` TypedDict, `check()`, `verdict_from()`, `to_dict()`/`from_dict()` helpers shared by all validator nodes
- `pipeline/nodes/validators/pre.py` — `node_pre_validator`
- `pipeline/nodes/validators/branch_research.py` — `node_bv_research`
- `pipeline/nodes/validators/branch_wordpress.py` — `node_bv_wordpress`
- `pipeline/nodes/validators/branch_youtube.py` — `node_bv_youtube`
- `pipeline/nodes/validators/summarizer.py` — `node_summarizer` (join + Telegram send)
- `tests/test_validators.py` — unit tests covering all five validator nodes

**Modified files:**
- `pipeline/state.py` — add per-branch state keys + `branch_validations_done` reducer
- `pipeline/graph.py` — rewire topology: replace single `validator` with pre + 3 branch + summarizer
- `pipeline/nodes/validator.py` — **deleted** in final task

---

## Task 1: Add new state keys

**Files:**
- Modify: `pipeline/state.py:80-87`

- [ ] **Step 1: Read current state file**

Run: `cat pipeline/state.py`
Confirm Stage 6 block ends at line 87.

- [ ] **Step 2: Replace Stage 6 block**

Replace lines 80–86 in `pipeline/state.py` with:

```python
    # Stage 6 — pre-validator (runs once after kb_curator)
    pre_findings: List[Dict[str, Any]]
    pre_verdict: str  # PASS | PARTIAL | FAIL

    # Stage 6 — per-branch validators (one per terminal branch)
    branch_findings_research: List[Dict[str, Any]]
    branch_verdict_research: str
    branch_findings_wordpress: List[Dict[str, Any]]
    branch_verdict_wordpress: str
    branch_findings_youtube: List[Dict[str, Any]]
    branch_verdict_youtube: str

    # Summarizer join: each branch validator appends its name. Reducer-backed
    # so the three parallel writes merge instead of overwriting.
    branch_validations_done: Annotated[List[str], add]

    # Stage 6 — summarizer (set on final fire only)
    validator_verdict: str  # PASS | PARTIAL | FAIL — aggregate across all branches
    validator_report_path: str
    pipeline_summary_path: str
```

- [ ] **Step 3: Verify imports still cover types used**

Confirm `pipeline/state.py:9` already has `from typing import Annotated, Any, Dict, List, Optional, TypedDict`. No import change needed.

- [ ] **Step 4: Run unittest discovery to verify module still loads**

Run: `python -c "from pipeline.state import State; print(sorted(State.__annotations__.keys()))"`
Expected: prints sorted key list including `branch_validations_done`, `pre_findings`, `branch_verdict_research`, `branch_verdict_wordpress`, `branch_verdict_youtube`, `validator_verdict`. No exception.

- [ ] **Step 5: Commit**

```bash
git add pipeline/state.py
git commit -m "state: add per-branch validator + summarizer keys"
```

---

## Task 2: Create validators package + shared helpers

**Files:**
- Create: `pipeline/nodes/validators/__init__.py`
- Create: `pipeline/nodes/validators/shared.py`
- Test: `tests/test_validators.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_validators.py`:

```python
"""Tests for the per-branch validator nodes + summarizer.

No network, no Telegram. Pure state-in/state-out.

Run:  python -m unittest tests.test_validators
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pipeline.nodes.validators.shared import (  # noqa: E402
    check,
    verdict_from,
)


class SharedHelpersTest(unittest.TestCase):
    def test_check_returns_dict(self):
        f = check("x", True, "detail", "core")
        self.assertEqual(f, {"name": "x", "ok": True, "detail": "detail", "severity": "core"})

    def test_check_default_severity_is_core(self):
        f = check("x", True)
        self.assertEqual(f["severity"], "core")

    def test_verdict_pass_when_all_ok(self):
        findings = [check("a", True, severity="core"), check("b", True, severity="secondary")]
        self.assertEqual(verdict_from(findings), "PASS")

    def test_verdict_partial_when_only_secondary_fails(self):
        findings = [check("a", True, severity="core"), check("b", False, severity="secondary")]
        self.assertEqual(verdict_from(findings), "PARTIAL")

    def test_verdict_fail_when_any_core_fails(self):
        findings = [check("a", False, severity="core"), check("b", True, severity="secondary")]
        self.assertEqual(verdict_from(findings), "FAIL")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_validators -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'pipeline.nodes.validators'`

- [ ] **Step 3: Create package marker**

Create `pipeline/nodes/validators/__init__.py`:

```python
"""Per-branch validator nodes + summarizer join.

Replaces the single fan-in ``pipeline.nodes.validator`` (now deleted).
See ``docs/superpowers/plans/2026-05-18-per-branch-validators.md``.
"""
```

- [ ] **Step 4: Write shared helpers**

Create `pipeline/nodes/validators/shared.py`:

```python
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
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m unittest tests.test_validators -v`
Expected: 5 tests, all PASS.

- [ ] **Step 6: Commit**

```bash
git add pipeline/nodes/validators/__init__.py pipeline/nodes/validators/shared.py tests/test_validators.py
git commit -m "validators: shared Finding helpers + verdict aggregator"
```

---

## Task 3: Pre-validator node

**Files:**
- Create: `pipeline/nodes/validators/pre.py`
- Test: append to `tests/test_validators.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_validators.py` (before the `if __name__` block):

```python
from pipeline.nodes.validators.pre import node_pre_validator  # noqa: E402


class PreValidatorTest(unittest.TestCase):
    def _state(self, **overrides):
        base = {
            "run_id": "2026-05-18_120000_001",
            "transcript_path": "",
            "transcript_word_count": 0,
            "extraction_report_path": "",
            "content_quality": "",
            "vault_entry_path": "",
            "vault_entry_slug": "",
            "video_date": "2026-05-18",
            "themes_attached": [],
            "frameworks_attached": [],
        }
        base.update(overrides)
        return base

    def test_missing_extraction_report_is_core_fail(self):
        out = node_pre_validator(self._state())
        names = [f["name"] for f in out["pre_findings"] if not f["ok"] and f["severity"] == "core"]
        self.assertIn("extraction_report.md exists", names)
        self.assertEqual(out["pre_verdict"], "FAIL")

    def test_missing_vault_entry_is_core_fail(self):
        out = node_pre_validator(self._state())
        names = [f["name"] for f in out["pre_findings"] if not f["ok"] and f["severity"] == "core"]
        self.assertIn("vault entry exists", names)

    def test_unknown_content_quality_is_secondary(self):
        out = node_pre_validator(self._state(content_quality="???"))
        sec_failed = [
            f for f in out["pre_findings"]
            if not f["ok"] and f["severity"] == "secondary" and f["name"] == "Content Quality classified"
        ]
        self.assertEqual(len(sec_failed), 1)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m unittest tests.test_validators -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'pipeline.nodes.validators.pre'`

- [ ] **Step 3: Implement pre-validator**

Create `pipeline/nodes/validators/pre.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m unittest tests.test_validators -v`
Expected: 8 tests, all PASS.

- [ ] **Step 5: Commit**

```bash
git add pipeline/nodes/validators/pre.py tests/test_validators.py
git commit -m "validators: pre_validator for transcript/extraction/vault checks"
```

---

## Task 4: Research branch validator

**Files:**
- Create: `pipeline/nodes/validators/branch_research.py`
- Test: append to `tests/test_validators.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_validators.py` (before `if __name__`):

```python
import tempfile  # noqa: E402

from pipeline.nodes.validators.branch_research import node_bv_research  # noqa: E402


class BvResearchTest(unittest.TestCase):
    def test_missing_csv_is_core_fail(self):
        out = node_bv_research({"research_csv_path": "", "notion_task_count": 0})
        self.assertEqual(out["branch_verdict_research"], "FAIL")
        self.assertEqual(out["branch_validations_done"], ["research"])
        names = [f["name"] for f in out["branch_findings_research"] if not f["ok"]]
        self.assertIn("research_report.csv exists", names)

    def test_csv_present_with_matching_notion_count_passes(self):
        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False) as fh:
            fh.write("title,url\nA,https://a\nB,https://b\n")
            csv_path = fh.name
        out = node_bv_research({"research_csv_path": csv_path, "notion_task_count": 2})
        self.assertEqual(out["branch_verdict_research"], "PASS")
        self.assertEqual(out["branch_validations_done"], ["research"])

    def test_csv_present_but_notion_count_mismatch_fails(self):
        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False) as fh:
            fh.write("title,url\nA,https://a\n")
            csv_path = fh.name
        out = node_bv_research({"research_csv_path": csv_path, "notion_task_count": 0})
        self.assertEqual(out["branch_verdict_research"], "FAIL")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m unittest tests.test_validators -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'pipeline.nodes.validators.branch_research'`

- [ ] **Step 3: Implement branch validator**

Create `pipeline/nodes/validators/branch_research.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m unittest tests.test_validators -v`
Expected: 11 tests, all PASS.

- [ ] **Step 5: Commit**

```bash
git add pipeline/nodes/validators/branch_research.py tests/test_validators.py
git commit -m "validators: research branch validator"
```

---

## Task 5: WordPress branch validator

**Files:**
- Create: `pipeline/nodes/validators/branch_wordpress.py`
- Test: append to `tests/test_validators.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_validators.py` (before `if __name__`):

```python
from unittest.mock import patch  # noqa: E402

from pipeline.nodes.validators.branch_wordpress import node_bv_wordpress  # noqa: E402


class BvWordpressTest(unittest.TestCase):
    def test_missing_blog_post_is_core_fail(self):
        out = node_bv_wordpress({"blog_post_path": "", "notion_blog_url": ""})
        self.assertEqual(out["branch_verdict_wordpress"], "FAIL")
        self.assertEqual(out["branch_validations_done"], ["wordpress"])

    def test_missing_notion_blog_url_is_core_fail(self):
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as fh:
            fh.write("# blog\n")
            bp = fh.name
        out = node_bv_wordpress({"blog_post_path": bp, "notion_blog_url": ""})
        self.assertEqual(out["branch_verdict_wordpress"], "FAIL")

    def test_dormant_wordpress_is_not_a_failure(self):
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as fh:
            fh.write("# blog\n")
            bp = fh.name
        with patch("pipeline.nodes.validators.branch_wordpress.fetch_page_blocks", return_value=[{"x": 1}]):
            out = node_bv_wordpress({
                "blog_post_path": bp,
                "notion_blog_url": "https://www.notion.so/foo-abc123",
                "wordpress_dormant": True,
                "image_extraction_failed": True,
            })
        self.assertEqual(out["branch_verdict_wordpress"], "PASS")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m unittest tests.test_validators -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'pipeline.nodes.validators.branch_wordpress'`

- [ ] **Step 3: Implement branch validator**

Create `pipeline/nodes/validators/branch_wordpress.py`:

```python
"""Branch validator: blog lineage (writer → notion_blog → wordpress_draft).

Also owns featured image check (extract_image joins into wordpress_draft).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from pipeline.notion_client import fetch_page_blocks
from pipeline.nodes.validators.shared import check, verdict_from
from pipeline.state import State


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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m unittest tests.test_validators -v`
Expected: 14 tests, all PASS.

- [ ] **Step 5: Commit**

```bash
git add pipeline/nodes/validators/branch_wordpress.py tests/test_validators.py
git commit -m "validators: wordpress branch validator"
```

---

## Task 6: YouTube branch validator

**Files:**
- Create: `pipeline/nodes/validators/branch_youtube.py`
- Test: append to `tests/test_validators.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_validators.py` (before `if __name__`):

```python
from pipeline.nodes.validators.branch_youtube import node_bv_youtube  # noqa: E402


class BvYoutubeTest(unittest.TestCase):
    def test_url_present_passes(self):
        out = node_bv_youtube({"youtube_url": "https://youtu.be/abc"})
        self.assertEqual(out["branch_verdict_youtube"], "PASS")
        self.assertEqual(out["branch_validations_done"], ["youtube"])

    def test_skipped_with_reason_passes_secondary(self):
        out = node_bv_youtube({"youtube_url": "", "youtube_skipped": True, "youtube_skip_reason": "dormant"})
        self.assertEqual(out["branch_verdict_youtube"], "PASS")

    def test_silent_failure_is_secondary_partial(self):
        out = node_bv_youtube({"youtube_url": "", "youtube_skipped": False})
        self.assertEqual(out["branch_verdict_youtube"], "PARTIAL")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m unittest tests.test_validators -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'pipeline.nodes.validators.branch_youtube'`

- [ ] **Step 3: Implement branch validator**

Create `pipeline/nodes/validators/branch_youtube.py`:

```python
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

    if state.get("youtube_url"):
        findings.append(check("YouTube upload produced URL or skipped cleanly", True, state["youtube_url"], severity="secondary"))
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m unittest tests.test_validators -v`
Expected: 17 tests, all PASS.

- [ ] **Step 5: Commit**

```bash
git add pipeline/nodes/validators/branch_youtube.py tests/test_validators.py
git commit -m "validators: youtube branch validator"
```

---

## Task 7: Summarizer (join + Telegram)

**Files:**
- Create: `pipeline/nodes/validators/summarizer.py`
- Test: append to `tests/test_validators.py`

The summarizer fires once per branch validator (3 inbound edges). It is idempotent for fires where `branch_validations_done` has not yet reached 3 (no Telegram, no file writes, returns empty state delta). On the 3rd fire it aggregates, writes files, sends Telegram once.

- [ ] **Step 1: Write failing tests**

Append to `tests/test_validators.py` (before `if __name__`):

```python
from pipeline.nodes.validators.summarizer import node_summarizer  # noqa: E402


class SummarizerTest(unittest.TestCase):
    def _state(self, done, **overrides):
        base = {
            "run_id": "2026-05-18_120000_001",
            "run_dir": tempfile.mkdtemp(prefix="sumtest_"),
            "transcript_path": "/x/transcript_t.md",
            "video_date": "2026-05-18",
            "content_quality": "Strong",
            "vault_entry_slug": "demo-slug",
            "blog_post_title": "Demo",
            "blog_post_path": "/x/blog.md",
            "notion_blog_url": "https://www.notion.so/demo-abc",
            "notion_task_count": 2,
            "research_csv_path": "/x/research.csv",
            "youtube_skipped": True,
            "youtube_skip_reason": "dormant",
            "wordpress_skipped": True,
            "wordpress_skip_reason": "no blog post",
            "image_extraction_failed": True,
            "branch_validations_done": done,
            "pre_findings": [],
            "pre_verdict": "PASS",
            "branch_findings_research": [],
            "branch_verdict_research": "PASS",
            "branch_findings_wordpress": [],
            "branch_verdict_wordpress": "PASS",
            "branch_findings_youtube": [],
            "branch_verdict_youtube": "PASS",
        }
        base.update(overrides)
        return base

    def test_no_op_when_fewer_than_three_branches_done(self):
        with patch("pipeline.nodes.validators.summarizer.telegram_send") as ts:
            out = node_summarizer(self._state(done=["research"]))
        self.assertEqual(out, {})
        ts.assert_not_called()

    def test_no_op_on_two_of_three(self):
        with patch("pipeline.nodes.validators.summarizer.telegram_send") as ts:
            out = node_summarizer(self._state(done=["research", "youtube"]))
        self.assertEqual(out, {})
        ts.assert_not_called()

    def test_emits_summary_on_third_fire(self):
        with patch("pipeline.nodes.validators.summarizer.telegram_send", return_value=0) as ts:
            out = node_summarizer(self._state(done=["research", "youtube", "wordpress"]))
        self.assertEqual(out["validator_verdict"], "PASS")
        self.assertTrue(out["validator_report_path"].endswith("audit_report.md"))
        self.assertTrue(out["pipeline_summary_path"].endswith("pipeline_summary.md"))
        ts.assert_called_once()

    def test_aggregated_verdict_is_fail_if_any_branch_fails(self):
        with patch("pipeline.nodes.validators.summarizer.telegram_send", return_value=0):
            out = node_summarizer(self._state(
                done=["research", "youtube", "wordpress"],
                branch_verdict_research="FAIL",
            ))
        self.assertEqual(out["validator_verdict"], "FAIL")

    def test_aggregated_verdict_is_partial_if_any_branch_partial(self):
        with patch("pipeline.nodes.validators.summarizer.telegram_send", return_value=0):
            out = node_summarizer(self._state(
                done=["research", "youtube", "wordpress"],
                branch_verdict_youtube="PARTIAL",
            ))
        self.assertEqual(out["validator_verdict"], "PARTIAL")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m unittest tests.test_validators -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'pipeline.nodes.validators.summarizer'`

- [ ] **Step 3: Implement summarizer**

Create `pipeline/nodes/validators/summarizer.py`:

```python
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
        return f"Stage 7 — wordpress draft:  ✓ {state['wordpress_url']}"
    if state.get("wordpress_dormant"):
        return "Stage 7 — wordpress draft:  ⏸ dormant (WORDPRESS_ENABLED!=true; bundle on disk)"
    if state.get("wordpress_skipped"):
        reason = state.get("wordpress_skip_reason", "skipped")
        return f"Stage 7 — wordpress draft:  ⏭ skipped ({reason})"
    return "Stage 7 — wordpress draft:  — (not run)"


def _yt_line(state: State) -> str:
    if state.get("youtube_url"):
        return f"Stage 8 — youtube upload:   ✓ {state['youtube_url']}"
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m unittest tests.test_validators -v`
Expected: 22 tests, all PASS.

- [ ] **Step 5: Commit**

```bash
git add pipeline/nodes/validators/summarizer.py tests/test_validators.py
git commit -m "validators: summarizer join with fire-per-parent idempotency"
```

---

## Task 8: Rewire graph

**Files:**
- Modify: `pipeline/graph.py`

- [ ] **Step 1: Read current graph.py to confirm line numbers**

Run: `cat pipeline/graph.py`
Confirm imports block ends at line 51 and node-add/edge block lives in `build_graph`.

- [ ] **Step 2: Replace validator import**

In `pipeline/graph.py:48`, replace:

```python
from pipeline.nodes.validator import node_validator
```

with:

```python
from pipeline.nodes.validators.branch_research import node_bv_research
from pipeline.nodes.validators.branch_wordpress import node_bv_wordpress
from pipeline.nodes.validators.branch_youtube import node_bv_youtube
from pipeline.nodes.validators.pre import node_pre_validator
from pipeline.nodes.validators.summarizer import node_summarizer
```

- [ ] **Step 3: Replace node registration**

In `pipeline/graph.py:137`, replace:

```python
    # Validator does no I/O retry-worth: it just inspects state + sends a
    # summary that already retries internally.
    g.add_node("validator", node_validator)
```

with:

```python
    # Per-branch validators + summarizer join. None need retry: they only
    # inspect state and (for summarizer) send Telegram with internal retry.
    g.add_node("pre_validator", node_pre_validator)
    g.add_node("bv_research", node_bv_research)
    g.add_node("bv_wordpress", node_bv_wordpress)
    g.add_node("bv_youtube", node_bv_youtube)
    g.add_node("summarizer", node_summarizer)
```

- [ ] **Step 4: Replace edges**

In `pipeline/graph.py:142-156`, replace:

```python
    g.add_edge("extract", "kb_curator")
    g.add_edge("extract", "extract_image")
    g.add_edge("extract", "youtube_upload")
    g.add_edge("kb_curator", "writer")
    g.add_edge("kb_curator", "research")
    g.add_edge("writer", "notion_blog")
    g.add_edge("research", "notion_research")
    # wordpress_draft synchronises on both notion_blog (page id) and
    # extract_image (featured image). LangGraph waits on both.
    g.add_edge("notion_blog", "wordpress_draft")
    g.add_edge("extract_image", "wordpress_draft")
    g.add_edge("wordpress_draft", "validator")
    g.add_edge("notion_research", "validator")
    g.add_edge("youtube_upload", "validator")
    g.add_edge("validator", END)
```

with:

```python
    g.add_edge("extract", "kb_curator")
    g.add_edge("extract", "extract_image")
    g.add_edge("extract", "youtube_upload")
    # pre_validator runs once between kb_curator and the writer/research fan-out
    # so shared upstream findings are computed exactly once.
    g.add_edge("kb_curator", "pre_validator")
    g.add_edge("pre_validator", "writer")
    g.add_edge("pre_validator", "research")
    g.add_edge("writer", "notion_blog")
    g.add_edge("research", "notion_research")
    g.add_edge("notion_blog", "wordpress_draft")
    g.add_edge("extract_image", "wordpress_draft")
    # Per-branch validators at the tail of each terminal branch.
    g.add_edge("wordpress_draft", "bv_wordpress")
    g.add_edge("notion_research", "bv_research")
    g.add_edge("youtube_upload", "bv_youtube")
    # Summarizer is a join: 3 inbound edges. LangGraph fires it once per parent
    # completion; the node is idempotent on early fires (see summarizer.py).
    g.add_edge("bv_wordpress", "summarizer")
    g.add_edge("bv_research", "summarizer")
    g.add_edge("bv_youtube", "summarizer")
    g.add_edge("summarizer", END)
```

- [ ] **Step 5: Update topology docstring**

Replace the ASCII diagram at `pipeline/graph.py:3-12` with:

```python
"""LangGraph DAG for the content pipeline.

Topology (per-branch validators + summarizer join):

    START → transcribe → extract ─┬─▶ kb_curator → pre_validator ─┬─▶ writer ─▶ notion_blog ─┐
                                  │                               └─▶ research ─▶ notion_research ─▶ bv_research ──┐
                                  ├─▶ extract_image ──────────────────────────────────────────┐                    │
                                  └─▶ youtube_upload ─▶ bv_youtube ────────────────────────────┼────────────────────┤
                                                                                               ▼                    │
                                                                                       wordpress_draft ─▶ bv_wordpress ─▶ summarizer → END
                                                                                                                          ▲
                                                                                                                          │
                                                                                                                bv_research ┘
"""
```

- [ ] **Step 6: Verify graph compiles**

Run: `python -c "from pipeline.graph import build_graph; g, s = build_graph(); print('ok'); s.conn.close()"`
Expected: prints `ok`. No `ValueError` from missing nodes/edges.

- [ ] **Step 7: Run full test suite**

Run: `python -m unittest discover -s tests -v`
Expected: all tests PASS (existing + new validator tests).

- [ ] **Step 8: Commit**

```bash
git add pipeline/graph.py
git commit -m "graph: rewire to pre_validator + 3 branch validators + summarizer join"
```

---

## Task 9: Delete old validator module

**Files:**
- Delete: `pipeline/nodes/validator.py`

- [ ] **Step 1: Confirm no remaining imports**

Run: `grep -rn "from pipeline.nodes.validator import\|pipeline.nodes.validator " pipeline tests`
Expected: zero matches. If any, stop and fix the importer before deleting.

- [ ] **Step 2: Delete the file**

Run: `git rm pipeline/nodes/validator.py`

- [ ] **Step 3: Run full test suite**

Run: `python -m unittest discover -s tests -v`
Expected: all tests PASS.

- [ ] **Step 4: Compile graph one more time**

Run: `python -c "from pipeline.graph import build_graph; g, s = build_graph(); print('ok'); s.conn.close()"`
Expected: prints `ok`.

- [ ] **Step 5: Commit**

```bash
git commit -m "validator: remove legacy single fan-in validator (replaced by per-branch)"
```

---

## Task 10: End-to-end smoke test

**Files:**
- Run: `tests/smoke_pipeline.sh`

- [ ] **Step 1: Read smoke script to know what it does**

Run: `cat tests/smoke_pipeline.sh`

- [ ] **Step 2: Run smoke pipeline**

Run: `bash tests/smoke_pipeline.sh`
Expected: exit 0. The Telegram channel receives **exactly one** message (PASS or PARTIAL). No partial-state messages from earlier in the run.

- [ ] **Step 3: Verify summary file matches Telegram**

Inspect the latest `runs/<run_id>/pipeline-summary/pipeline_summary.md` — content should match the Telegram message exactly (same verdict line, same stage breakdown).

- [ ] **Step 4: Verify audit report includes all branch verdicts**

Inspect the latest `runs/<run_id>/validator/audit_report.md` — should include the four lines `**Pre-validator verdict:**`, `**Research branch verdict:**`, `**WordPress branch verdict:**`, `**YouTube branch verdict:**`.

- [ ] **Step 5: Commit nothing if everything passes**

If smoke passes, no commit. If anything fails, fix the root cause in a new task — do not paper over with conditional logic in the summarizer.

---

## Self-Review Notes

- **Spec coverage:** All 3 user-named outcomes (notion_research, wordpress_draft, youtube_upload) get a dedicated branch validator. Shared upstream checks moved to pre_validator (avoids triple-counting). Single Telegram preserved via summarizer-with-idempotency.
- **Failure isolation:** A branch validator failing produces a FAIL verdict for THAT branch only; other branches still validate. Summarizer aggregates whatever arrived.
- **Stale-state risk eliminated:** Each branch validator only inspects state keys its branch produces — those keys are populated by the time its branch's terminal node finishes. No more "blog_post.md missing" false negatives from fan-in firing too early.
- **Backward compat:** `validator_verdict`, `validator_report_path`, `pipeline_summary_path` keys preserved; `run.py:362` reads `validator_verdict` and still works.
- **No placeholders:** every code block is complete; every command lists expected output.
