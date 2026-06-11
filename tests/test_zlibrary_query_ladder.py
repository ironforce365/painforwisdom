"""Fix C — z-lib query ladder must strip curator title noise.

The ladder used its own `_normalize_title`, which only handled em/en-dashes,
parenthetical editions, and `Ch N:` — so regular-hyphen separators, double
hyphens, standalone "Nth Edition", and "Book N Section M" passage citations
survived into the search query and returned 0 hits. 2026-06-10 casualties:

  "Lore of Running 4th Edition - Chapter 2: Oxygen Transport"
  "Marcus Aurelius -- Meditations Book 5 Section 20"
  "Opening Up by Writing It Down (3rd ed.): How Expressive Writing…"

`canonical_book_title()` (pipeline/local_books.py, added #56) already splits on
` - ` / ` -- ` / `:` and strips parens — reuse it so a clean core appears in the
ladder.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline import zlibrary_bridge as zb  # noqa: E402


class QueryLadderCleansNoiseTests(unittest.TestCase):
    def _ladder(self, title: str, author: str = "Some Author"):
        return zb._build_query_ladder(title, author)

    def test_chapter_and_topic_noise_dropped(self):
        ladder = self._ladder("Lore of Running 4th Edition - Chapter 2: Oxygen Transport", "Timothy Noakes")
        # No rung may carry the chapter topic or the word "Chapter".
        for q in ladder:
            self.assertNotIn("Oxygen Transport", q)
            self.assertNotIn("Chapter", q)
        # A clean core rung must exist.
        self.assertTrue(any("lore of running" in q.lower() for q in ladder), ladder)

    def test_double_hyphen_section_citation_dropped(self):
        ladder = self._ladder("Marcus Aurelius -- Meditations Book 5 Section 20", "Marcus Aurelius")
        for q in ladder:
            self.assertNotIn("Section", q)
            self.assertNotIn("Book 5", q)

    def test_parenthetical_edition_and_subtitle_dropped(self):
        ladder = self._ladder(
            "Opening Up by Writing It Down (3rd ed.): How Expressive Writing Improves Health",
            "James W. Pennebaker",
        )
        self.assertTrue(any(q.lower().strip() == "opening up by writing it down" for q in ladder), ladder)
        for q in ladder:
            self.assertNotIn("3rd ed", q.lower())
            self.assertNotIn("How Expressive", q)

    def test_clean_title_unharmed(self):
        ladder = self._ladder("Atomic Habits", "James Clear")
        self.assertTrue(ladder)
        self.assertIn("Atomic Habits James Clear", ladder)


if __name__ == "__main__":
    unittest.main()
