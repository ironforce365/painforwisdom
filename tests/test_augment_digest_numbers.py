"""Telegram digest = operational NUMBERS only (2026-06-08 feedback).

The per-title bullet lists ("• The Talent Code — Ch. 2 …", "… and 21 more")
gave Gonzalo nothing to act on. The digest must show only the counts that say
where we are: how many downloaded, how many pending, how many need manual
sourcing. The full titles still live in the augment-failures-*.jsonl for anyone
who wants them.
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


class DigestNumbersOnlyTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def _send(self, **overrides) -> str:
        sent: dict = {}
        base: Dict[str, Any] = dict(
            book_failures=[],
            quota_skipped=[],
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

    def test_no_book_titles_or_bullets_in_message(self):
        msg = self._send(
            book_failures=[{"title": "Opening Up by Writing It Down", "reason": "not-found"}],
            quota_skipped=[{"title": "The Talent Code — Ch. 2", "reason": "quota"}],
        )
        self.assertNotIn("Opening Up by Writing It Down", msg)
        self.assertNotIn("The Talent Code", msg)
        self.assertNotIn("•", msg)
        self.assertNotIn("and 21 more", msg)

    def test_shows_downloaded_and_pending_counts(self):
        msg = self._send(
            n_zlib_hit=1,
            n_curated_local=1,
            quota_skipped=[{"title": f"B{i}", "reason": "quota"} for i in range(26)],
            book_failures=[{"title": "X", "reason": "not-found"} for _ in range(3)],
        )
        # downloaded count present
        self.assertIn("1", msg)
        # pending count present and labeled
        self.assertRegex(msg.lower(), r"pending[^\n]*26")
        # manual-sourcing count present
        self.assertRegex(msg.lower(), r"(manual sourcing|need)[^\n]*3")
        # still points at the detail file
        self.assertIn("augment-failures-", msg)

    def test_green_when_only_quota_deferred(self):
        msg = self._send(quota_skipped=[{"title": "x", "reason": "q"} for _ in range(5)])
        self.assertTrue(msg.startswith("✅"), msg.splitlines()[0])

    def test_yellow_when_genuine_failures(self):
        msg = self._send(book_failures=[{"title": "x", "reason": "not-found"}])
        self.assertTrue(msg.startswith("🟡"), msg.splitlines()[0])

    def test_message_is_compact(self):
        # 26 deferred + 33 failed must NOT balloon the message — numbers only.
        msg = self._send(
            quota_skipped=[{"title": f"B{i}", "reason": "q"} for i in range(26)],
            book_failures=[{"title": f"F{i}", "reason": "not-found"} for i in range(33)],
        )
        self.assertLessEqual(len(msg.splitlines()), 8)


if __name__ == "__main__":
    unittest.main()
