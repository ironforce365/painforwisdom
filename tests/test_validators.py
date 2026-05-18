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


if __name__ == "__main__":
    unittest.main()
