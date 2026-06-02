"""Regression tests for book-level dedup in augment-research (2026-06-02).

Chapter/edition rows of one book collapsed to a single fetch so we don't
re-search + re-download the identical book per chapter (wastes z-lib quota +
login load). Keyed on (normalized-title, first-author-lastname).
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.scripts.augment_research_tasks import _book_identity  # noqa: E402


def _id(title: str, author: str = ""):
    return _book_identity({"title": title, "author_host": author})


class BookIdentityTests(unittest.TestCase):
    def test_chapters_of_same_book_collapse(self):
        a = _id("Do Hard Things — Ch. 2: Sink or Swim", "Steve Magness")
        b = _id('Do Hard Things -- Ch. 6 "Your Emotions Are Messengers"', "Steve Magness")
        c = _id("Do Hard Things — Pillar 4 (Ch. 10–11): Build", "Steve Magness")
        self.assertEqual(a, b)
        self.assertEqual(a, c)

    def test_subtitle_and_bare_title_collapse(self):
        full = _id("The Obstacle Is the Way: The Timeless Art of Turning Trials", "Ryan Holiday")
        bare = _id("The Obstacle Is the Way", "Ryan Holiday")
        self.assertEqual(full, bare)

    def test_parenthetical_annotation_collapses(self):
        ann = _id("The Happiness Trap (Values vs Goals)", "Russ Harris")
        chap = _id("The Happiness Trap — Ch. 31: Willingness", "Russ Harris")
        self.assertEqual(ann, chap)

    def test_edition_suffix_collapses(self):
        e2 = _id("Running Rewired (2nd Ed.) — neuromuscular imbalances", "Jay Dicharry")
        plain = _id("Running Rewired", "Jay Dicharry")
        self.assertEqual(e2, plain)

    def test_different_books_do_not_merge(self):
        # Same author, genuinely different books must stay distinct.
        self.assertNotEqual(
            _id("Peak", "Anders Ericsson"),
            _id("Peak Performance", "Anders Ericsson"),
        )
        # Same title-ish, different author must stay distinct.
        self.assertNotEqual(
            _id("Willpower", "Roy Baumeister"),
            _id("Willpower", "Kelly McGonigal"),
        )

    def test_empty_row_is_stable(self):
        self.assertEqual(_id("", ""), ("", ""))


if __name__ == "__main__":
    unittest.main()
