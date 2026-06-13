"""Telegram digest = operational NUMBERS with a real backlog DENOMINATOR.

2026-06-10 feedback, two rounds:

1. "I don't understand what 'need manual sourcing' means" + it was lumping in
   tooling crashes and reachable books. Manual = genuinely-unreachable books a
   human must find (paywalled / no URL / not on z-lib). subprocess-error,
   extract-failed, and reachable `_zlib_only` misses all auto-retry.

2. "it still doesn't tell me how many books from the backlog are still pending
   to be downloaded." → the digest must show the DENOMINATOR: of all book rows,
   how many now have a local full-text copy vs how many are still missing.

       📚 full-text copies: 182 / 189 books  (7 still missing)
          • 4 retry automatically (readable now; offline copy pending)
          • 3 need you to source (paywalled / no URL / not on z-lib)

Full titles still live in augment-failures-*.jsonl.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.scripts import augment_research_tasks as art  # noqa: E402


class TransientClassifierTests(unittest.TestCase):
    def test_subprocess_error_is_transient(self):
        self.assertTrue(art._is_transient_zlib_failure("z-library: subprocess-error (crash)"))

    def test_extract_failed_is_transient(self):
        self.assertTrue(art._is_transient_zlib_failure("z-library: extract-failed (no path)"))

    def test_not_found_is_not_transient(self):
        self.assertFalse(art._is_transient_zlib_failure("z-library: not-found (0 results)"))

    def test_not_configured_is_not_transient(self):
        self.assertFalse(art._is_transient_zlib_failure("z-library: not-configured"))

    def test_image_only_pdf_is_not_transient(self):
        self.assertFalse(art._is_transient_zlib_failure("z-library: image-only-pdf"))


class DigestCoverageTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def _send(self, **overrides) -> str:
        sent: dict = {}
        base: Dict[str, Any] = dict(
            book_failures=[],
            quota_skipped=[],
            transient_skipped=[],
            zlib_only_misses=[],
            n_books_total=50,
            n_zlib_hit=1,
            n_curated_local=1,
            n_dedup_reuse=0,
            n_success=2,
            n_halt=0,
            zlib_quota_exhausted=False,
        )
        base.update(overrides)
        with patch.object(art, "telegram_send", lambda m: sent.update(msg=m)), patch.object(
            art, "_format_zlib_quota_line", lambda: None
        ), patch.object(art, "AUGMENT_FAILURES_DIR", Path(self._tmp.name)):
            art._write_failures_and_notify(**base)
        return sent["msg"]

    # --- numbers only ---------------------------------------------------------

    def test_no_book_titles_in_message(self):
        msg = self._send(
            book_failures=[{"title": "Opening Up by Writing It Down", "reason": "not-found"}],
            n_books_total=189,
        )
        self.assertNotIn("Opening Up by Writing It Down", msg)
        self.assertNotIn("and 21 more", msg)

    # --- THE ASK: coverage denominator ---------------------------------------

    def test_coverage_line_shows_total_and_missing(self):
        # Today's real shape: 189 books, 7 still missing → 182 have a copy.
        msg = self._send(
            n_books_total=189,
            zlib_only_misses=[{"title": f"R{i}"} for i in range(4)],
            book_failures=[{"title": f"F{i}", "reason": "not-found"} for i in range(3)],
        )
        self.assertRegex(msg, r"182\s*/\s*189")
        self.assertRegex(msg.lower(), r"7\s*still missing")

    def test_split_counts_auto_vs_manual(self):
        msg = self._send(
            n_books_total=189,
            zlib_only_misses=[{"title": f"R{i}"} for i in range(4)],
            book_failures=[{"title": f"F{i}", "reason": "not-found"} for i in range(3)],
        )
        self.assertRegex(msg.lower(), r"4\s*retry automatically")
        self.assertRegex(msg.lower(), r"3\s*need you to source")

    def test_zlib_only_miss_and_transient_count_as_auto(self):
        msg = self._send(
            n_books_total=20,
            zlib_only_misses=[{"title": "Peak"}],
            transient_skipped=[{"title": "X", "reason": "subprocess-error"}],
            quota_skipped=[{"title": "Q", "reason": "quota"}],
            book_failures=[],
        )
        self.assertRegex(msg.lower(), r"3\s*retry automatically")
        self.assertRegex(msg.lower(), r"0\s*need you to source")

    # --- status colour --------------------------------------------------------

    def test_green_when_only_zlib_only_misses(self):
        # Reachable books missing an offline copy are not a red flag.
        msg = self._send(
            zlib_only_misses=[{"title": "Peak"}, {"title": "Lore of Running"}],
            book_failures=[],
        )
        self.assertTrue(msg.startswith("✅"), msg.splitlines()[0])

    def test_green_when_only_quota_deferred(self):
        msg = self._send(quota_skipped=[{"title": "x"} for _ in range(5)], book_failures=[])
        self.assertTrue(msg.startswith("✅"), msg.splitlines()[0])

    def test_yellow_when_genuine_manual(self):
        msg = self._send(book_failures=[{"title": "x", "reason": "not-found"}])
        self.assertTrue(msg.startswith("🟡"), msg.splitlines()[0])

    # --- detail file + compactness -------------------------------------------

    def test_points_at_detail_file(self):
        msg = self._send(book_failures=[{"title": "X", "reason": "not-found"}])
        self.assertIn("augment-failures-", msg)

    def test_message_is_compact(self):
        msg = self._send(
            n_books_total=189,
            quota_skipped=[{"title": f"B{i}"} for i in range(26)],
            transient_skipped=[{"title": f"T{i}"} for i in range(4)],
            zlib_only_misses=[{"title": f"R{i}"} for i in range(10)],
            book_failures=[{"title": f"F{i}", "reason": "not-found"} for i in range(33)],
            zlib_quota_exhausted=True,
        )
        self.assertLessEqual(len(msg.splitlines()), 9)


if __name__ == "__main__":
    unittest.main()
