"""Tests for pipeline.local_books — fuzzy match of references against books/.

No network. Uses a tmp `books/` directory mimicking the real layout.

Run: python -m unittest tests.test_local_books
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.local_books import (  # noqa: E402
    find_local_book,
    render_inventory_for_prompt,
    scan_inventory,
)


def _touch(p: Path, size: int = 1) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x" * size)


class LocalBooksTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "books"
        # Raw z-library dumps.
        _touch(self.root / "raw" / "HutchinsonAlex_Endure_2018_engli_3570783.epub", 1000)
        _touch(
            self.root / "raw"
            / "HutchinsonAlex_EndureMindBodyAndTheCuriouslyElasticLimitsOfHumanPerformance_2018_engli_24925483.epub",
            5000,
        )
        _touch(
            self.root / "raw"
            / "EasterMichael_TheComfortCrisisEmbraceDiscomfortToReclaimYourWildHappyHealthySelf_2021_engli_28353974.epub",
            2000,
        )
        _touch(
            self.root / "raw"
            / "BooksWorth_SummaryAndAnalysisOfMindsetTheNewPsychologyOfSuccessBasedOnTheBookByCarolSDweckPhd_2017_engli_23536117.epub",
            3000,
        )
        # Curated folder with matching slug.
        _touch(
            self.root / "peak-performance"
            / "MagnessBradStulbergSteve_PeakPerformance_2017_engli_115927698.epub",
            7000,
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_endure_matches_via_raw(self):
        r = find_local_book(
            "Endure: Mind, Body, and the Curiously Elastic Limits of Human Performance",
            "Alex Hutchinson",
            self.root,
        )
        self.assertIsNotNone(r)
        self.assertIn("Endure", r.path.name)

    def test_short_endure_title_still_matches(self):
        r = find_local_book("Endure", "Alex Hutchinson", self.root)
        self.assertIsNotNone(r)
        self.assertIn("Endure", r.path.name)

    def test_curated_folder_takes_priority(self):
        r = find_local_book("Peak Performance", "Steve Magness", self.root)
        self.assertIsNotNone(r)
        self.assertIn("peak-performance", str(r.path))

    def test_summary_book_with_wrong_author_rejected(self):
        # BooksWorth summary file has "carol" + "dweck" in the title chunk,
        # but cand_author = {books, worth} != {carol, dweck} — author guard
        # rejects it.
        r = find_local_book("Mindset", "Carol Dweck", self.root)
        self.assertIsNone(r)

    def test_unknown_book_returns_none(self):
        r = find_local_book("Atomic Habits", "James Clear", self.root)
        self.assertIsNone(r)

    def test_file_url_is_absolute(self):
        r = find_local_book("Endure", "Alex Hutchinson", self.root)
        self.assertTrue(r.file_url.startswith("file:///"))

    def test_scan_inventory_includes_all_raw_and_curated(self):
        inv = scan_inventory(self.root)
        # 4 raw + 1 curated.
        self.assertEqual(len(inv), 5)

    def test_render_inventory_lists_titles(self):
        text = render_inventory_for_prompt(self.root)
        self.assertIn("LOCAL BOOK INVENTORY", text)
        self.assertIn("Endure", text)
        self.assertIn("Peak Performance", text)
        self.assertIn("file://", text)

    def test_empty_inventory_returns_empty_string(self):
        empty = Path(self.tmp.name) / "empty-books"
        empty.mkdir()
        self.assertEqual(render_inventory_for_prompt(empty), "")


if __name__ == "__main__":
    unittest.main()
