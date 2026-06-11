"""Telegram digest = operational NUMBERS only, with a STANDING backlog line.

Two pieces of 2026-06-10 feedback drive this contract:

1. "I don't understand what 'need manual sourcing' means" — the digest must
   distinguish books the pipeline will retry on its own (quota/budget/transient
   tooling crash) from books that genuinely need a human (z-lib can't find them).

2. "that doesn't tell me how many more are pending to download" — the old digest
   only printed a pending line when z-lib quota ran out mid-run. On a normal day
   (downloads stop under the cap) the line vanished, so the standing backlog was
   invisible. The backlog line must ALWAYS be present:

       books still without a source: N
          • A auto-retry  (quota/budget — pipeline retries)
          • M need manual  (z-library can't find them — you source)

Full titles still live in augment-failures-*.jsonl for anyone who wants them.
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
    """subprocess-error / extract-failed are tooling crashes — the book may well
    download next run. They must NOT sit in the human 'manual sourcing' bucket."""

    def test_subprocess_error_is_transient(self):
        self.assertTrue(
            art._is_transient_zlib_failure("z-library: subprocess-error (bridge crashed)")
        )

    def test_extract_failed_is_transient(self):
        self.assertTrue(
            art._is_transient_zlib_failure("z-library: extract-failed (Download path missing)")
        )

    def test_not_found_is_not_transient(self):
        self.assertFalse(
            art._is_transient_zlib_failure("z-library: not-found (No author-matched results)")
        )

    def test_not_configured_is_not_transient(self):
        self.assertFalse(
            art._is_transient_zlib_failure("z-library: not-configured (creds unset)")
        )

    def test_image_only_pdf_is_not_transient(self):
        self.assertFalse(
            art._is_transient_zlib_failure("z-library: image-only-pdf (no text layer)")
        )


class DigestBacklogTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def _send(self, **overrides) -> str:
        sent: dict = {}
        base: Dict[str, Any] = dict(
            book_failures=[],
            quota_skipped=[],
            transient_skipped=[],
            n_zlib_hit=1,
            n_curated_local=1,
            n_dedup_reuse=0,
            n_success=2,
            n_halt=0,
            zlib_quota_exhausted=True,
        )
        base.update(overrides)
        with patch.object(art, "telegram_send", lambda m: sent.update(msg=m)), patch.object(
            art, "_format_zlib_quota_line", lambda: None
        ), patch.object(art, "AUGMENT_FAILURES_DIR", Path(self._tmp.name)):
            art._write_failures_and_notify(**base)
        return sent["msg"]

    # --- numbers only, no per-title lists -------------------------------------

    def test_no_book_titles_in_message(self):
        msg = self._send(
            book_failures=[{"title": "Opening Up by Writing It Down", "reason": "not-found"}],
            quota_skipped=[{"title": "The Talent Code — Ch. 2", "reason": "quota"}],
        )
        self.assertNotIn("Opening Up by Writing It Down", msg)
        self.assertNotIn("The Talent Code", msg)
        self.assertNotIn("and 21 more", msg)

    # --- THE BUG: standing backlog visible even with no quota-deferred ---------

    def test_backlog_line_present_when_no_quota_deferred(self):
        # Today's exact case: 7 genuine failures, 0 deferred. Old digest showed
        # NO count of what's left. New digest must say "still without a source: 7".
        msg = self._send(
            n_zlib_hit=6,
            n_curated_local=1,
            quota_skipped=[],
            transient_skipped=[],
            book_failures=[{"title": f"F{i}", "reason": "not-found"} for i in range(7)],
            zlib_quota_exhausted=False,
        )
        self.assertRegex(msg.lower(), r"still without a source:\s*7")
        self.assertRegex(msg.lower(), r"7\s*need manual")
        self.assertRegex(msg.lower(), r"0\s*auto-retry")

    def test_backlog_splits_auto_and_manual(self):
        msg = self._send(
            quota_skipped=[{"title": f"Q{i}", "reason": "quota"} for i in range(26)],
            transient_skipped=[],
            book_failures=[{"title": f"F{i}", "reason": "not-found"} for i in range(3)],
        )
        self.assertRegex(msg.lower(), r"still without a source:\s*29")
        self.assertRegex(msg.lower(), r"26\s*auto-retry")
        self.assertRegex(msg.lower(), r"3\s*need manual")

    def test_transient_counts_as_auto_retry_not_manual(self):
        # 2 tooling crashes (subprocess-error) + 0 genuine failures → total 2,
        # all auto-retry, manual 0.
        msg = self._send(
            quota_skipped=[],
            transient_skipped=[{"title": "Peak", "reason": "subprocess-error"} for _ in range(2)],
            book_failures=[],
            zlib_quota_exhausted=False,
        )
        self.assertRegex(msg.lower(), r"still without a source:\s*2")
        self.assertRegex(msg.lower(), r"2\s*auto-retry")
        self.assertRegex(msg.lower(), r"0\s*need manual")

    # --- status colour --------------------------------------------------------

    def test_green_when_only_quota_deferred(self):
        msg = self._send(
            quota_skipped=[{"title": "x", "reason": "q"} for _ in range(5)],
            book_failures=[],
        )
        self.assertTrue(msg.startswith("✅"), msg.splitlines()[0])

    def test_green_when_only_transient(self):
        # Tooling crashes are not a red flag — pipeline retries them.
        msg = self._send(
            transient_skipped=[{"title": "Peak", "reason": "subprocess-error"}],
            book_failures=[],
            zlib_quota_exhausted=False,
        )
        self.assertTrue(msg.startswith("✅"), msg.splitlines()[0])

    def test_yellow_when_genuine_failures(self):
        msg = self._send(
            book_failures=[{"title": "x", "reason": "not-found"}],
            zlib_quota_exhausted=False,
        )
        self.assertTrue(msg.startswith("🟡"), msg.splitlines()[0])

    # --- still points at the detail file, stays compact -----------------------

    def test_points_at_detail_file(self):
        msg = self._send(
            book_failures=[{"title": "X", "reason": "not-found"}],
            zlib_quota_exhausted=False,
        )
        self.assertIn("augment-failures-", msg)

    def test_message_is_compact(self):
        # Big numbers must NOT balloon the message — counts only.
        msg = self._send(
            quota_skipped=[{"title": f"B{i}", "reason": "q"} for i in range(26)],
            transient_skipped=[{"title": f"T{i}", "reason": "subprocess-error"} for i in range(4)],
            book_failures=[{"title": f"F{i}", "reason": "not-found"} for i in range(33)],
        )
        self.assertLessEqual(len(msg.splitlines()), 8)


if __name__ == "__main__":
    unittest.main()
