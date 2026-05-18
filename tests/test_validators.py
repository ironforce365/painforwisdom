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


from unittest.mock import patch  # noqa: E402

from pipeline.nodes.validators.branch_wordpress import node_bv_wordpress  # noqa: E402


class BvWordpressTest(unittest.TestCase):
    def test_premature_fire_returns_empty(self):
        # wordpress_draft fires after either extract_image OR notion_blog. If
        # neither blog_post_path nor notion_blog_url is set, this is the
        # early fire from extract_image — don't commit a verdict because
        # wordpress_draft will re-fire when notion_blog completes.
        out = node_bv_wordpress({"blog_post_path": "", "notion_blog_url": ""})
        self.assertEqual(out, {})

    def test_blog_present_but_notion_missing_is_core_fail(self):
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as fh:
            fh.write("# blog\n")
            bp = fh.name
        out = node_bv_wordpress({"blog_post_path": bp, "notion_blog_url": ""})
        self.assertEqual(out["branch_verdict_wordpress"], "FAIL")
        self.assertEqual(out["branch_validations_done"], ["wordpress"])

    def test_notion_present_but_blog_missing_is_core_fail(self):
        with patch("pipeline.nodes.validators.branch_wordpress.fetch_page_blocks", return_value=[{"x": 1}]):
            out = node_bv_wordpress({
                "blog_post_path": "",
                "notion_blog_url": "https://www.notion.so/foo-abc123",
            })
        self.assertEqual(out["branch_verdict_wordpress"], "FAIL")
        self.assertEqual(out["branch_validations_done"], ["wordpress"])

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

    def test_skip_when_validator_verdict_already_set(self):
        # Once summarizer has produced a verdict, subsequent fires must no-op
        # so re-fired upstream branches (e.g. wordpress_draft has 2 inbound
        # edges and may run twice) don't send a second Telegram message.
        with patch("pipeline.nodes.validators.summarizer.telegram_send") as ts:
            out = node_summarizer(self._state(
                done=["research", "youtube", "wordpress", "wordpress"],
                validator_verdict="PASS",
            ))
        self.assertEqual(out, {})
        ts.assert_not_called()


if __name__ == "__main__":
    unittest.main()
