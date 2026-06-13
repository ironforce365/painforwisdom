"""Fix B — a reachable book that misses z-lib must NOT be downgraded.

`_zlib_only` books are reachable via their own URL; z-library is only an
*optional* offline full-text copy. The old loop, on a z-lib miss, wrote
Reachable=no to Notion (excluding the source from daily briefs) and filed the
book under "need manual sourcing" — both wrong. `_classify_book_miss` is the
pure decision seam: which bucket, and whether to downgrade Notion state.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.scripts import augment_research_tasks as art  # noqa: E402


class ClassifyBookMissTests(unittest.TestCase):
    def test_zlib_only_never_downgrades(self):
        bucket, downgrade = art._classify_book_miss(
            is_zlib_only=True, skipped_by_quota=False, is_transient=False
        )
        self.assertEqual(bucket, "zlib_only_miss")
        self.assertFalse(downgrade)

    def test_zlib_only_wins_even_over_quota(self):
        # Reachable book skipped because quota was gone — still no downgrade.
        bucket, downgrade = art._classify_book_miss(
            is_zlib_only=True, skipped_by_quota=True, is_transient=False
        )
        self.assertEqual(bucket, "zlib_only_miss")
        self.assertFalse(downgrade)

    def test_unreachable_quota_defers(self):
        bucket, downgrade = art._classify_book_miss(
            is_zlib_only=False, skipped_by_quota=True, is_transient=False
        )
        self.assertEqual(bucket, "quota")
        self.assertTrue(downgrade)

    def test_unreachable_transient_defers(self):
        bucket, downgrade = art._classify_book_miss(
            is_zlib_only=False, skipped_by_quota=False, is_transient=True
        )
        self.assertEqual(bucket, "transient")
        self.assertTrue(downgrade)

    def test_unreachable_notfound_is_manual(self):
        bucket, downgrade = art._classify_book_miss(
            is_zlib_only=False, skipped_by_quota=False, is_transient=False
        )
        self.assertEqual(bucket, "manual")
        self.assertTrue(downgrade)


if __name__ == "__main__":
    unittest.main()
