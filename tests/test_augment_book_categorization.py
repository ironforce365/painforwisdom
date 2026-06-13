"""Fix B/B+ — a reachable book that misses z-lib is HEALED, not downgraded.

`_zlib_only` books are reachable via their own URL; z-library is only an
*optional* offline full-text copy. The old loop, on a z-lib miss, wrote
Reachable=no to Notion (excluding the source from daily briefs) and filed the
book under "need manual sourcing" — both wrong. Worse, books a prior buggy run
had already downgraded stayed stuck at no. So a reachable miss must actively
write Reachable=**yes** (heal), not merely skip the write.

`_classify_book_miss` is the pure seam: which digest bucket, and what Notion
Reachable state to write ("yes" heal / "no" downgrade / None leave-alone).
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.scripts import augment_research_tasks as art  # noqa: E402


class ClassifyBookMissTests(unittest.TestCase):
    def test_zlib_only_heals_to_yes(self):
        bucket, notion_state = art._classify_book_miss(
            is_zlib_only=True, skipped_by_quota=False, is_transient=False
        )
        self.assertEqual(bucket, "zlib_only_miss")
        self.assertEqual(notion_state, "yes")

    def test_zlib_only_heals_even_over_quota(self):
        # Reachable book skipped because quota was gone — still heal, never down.
        bucket, notion_state = art._classify_book_miss(
            is_zlib_only=True, skipped_by_quota=True, is_transient=False
        )
        self.assertEqual(bucket, "zlib_only_miss")
        self.assertEqual(notion_state, "yes")

    def test_unreachable_quota_defers_and_marks_no(self):
        bucket, notion_state = art._classify_book_miss(
            is_zlib_only=False, skipped_by_quota=True, is_transient=False
        )
        self.assertEqual(bucket, "quota")
        self.assertEqual(notion_state, "no")

    def test_unreachable_transient_defers_and_marks_no(self):
        bucket, notion_state = art._classify_book_miss(
            is_zlib_only=False, skipped_by_quota=False, is_transient=True
        )
        self.assertEqual(bucket, "transient")
        self.assertEqual(notion_state, "no")

    def test_unreachable_notfound_is_manual_and_marks_no(self):
        bucket, notion_state = art._classify_book_miss(
            is_zlib_only=False, skipped_by_quota=False, is_transient=False
        )
        self.assertEqual(bucket, "manual")
        self.assertEqual(notion_state, "no")


if __name__ == "__main__":
    unittest.main()
