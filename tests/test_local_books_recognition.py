"""Recognition tests for local_books (2026-06-08 daily quota-burn incident).

Root cause: every successful z-library download was re-attempted the next day
because `find_local_book` could not recognize it:
  1. extractions land in `books/extracted/`, which the scan SKIPPED entirely;
  2. chapter/edition/subtitle-annotated request titles ("The Talent Code —
     Ch. 2: ...") diluted token-overlap below threshold even when the parent
     book sat in `books/raw/`.
Net effect: the augment queue never drained, the 10/day cap was spent
re-downloading owned books, genuinely-missing books never got fetched.

These tests pin the two recognition fixes while guarding against the
false-positive that would serve the WRONG local book into a live brief
(`Peak` must not match `Peak Performance`).
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.local_books import canonical_book_title, find_local_book  # noqa: E402


class CanonicalBookTitleTests(unittest.TestCase):
    def test_strips_chapter_tail(self):
        self.assertEqual(
            canonical_book_title("The Talent Code — Ch. 2: The Deep Practice Cell"),
            "the talent code",
        )

    def test_strips_double_dash_chapter(self):
        self.assertEqual(
            canonical_book_title('Do Hard Things -- Ch. 6 "Your Emotions Are Messengers"'),
            "do hard things",
        )

    def test_strips_subtitle_after_colon(self):
        self.assertEqual(
            canonical_book_title("The Obstacle Is the Way: The Timeless Art of Turning Trials"),
            "the obstacle is the way",
        )

    def test_strips_parenthetical_and_edition(self):
        self.assertEqual(canonical_book_title("The Happiness Trap (Values vs Goals)"), "the happiness trap")
        self.assertEqual(
            canonical_book_title("Running Rewired (2nd Ed.) — neuromuscular imbalances"),
            "running rewired",
        )

    def test_bare_title_unchanged(self):
        self.assertEqual(canonical_book_title("Atomic Habits"), "atomic habits")


def _make_books(tmp: Path) -> Path:
    root = tmp / "books"
    (root / "raw").mkdir(parents=True)
    (root / "extracted").mkdir(parents=True)
    # raw/: z-library filename pattern AuthorChunk_TitleChunk_YYYY_lang_id.ext
    (root / "raw" / "CoyleDaniel_TheTalentCodeGreatnessIsntBornItSGrownHereSHow_2009_engli_111.epub").write_text("x")
    (root / "raw" / "MagnessSteve_DoHardThingsWhyWeGetResilienceWrong_2022_engli_222.epub").write_text("x")
    # extracted/: tidy title-slug .txt (no author encoded)
    (root / "extracted" / "80-20-running.txt").write_text("x")
    (root / "extracted" / "peak-performance.txt").write_text("x")
    return root


class FindLocalBookRecognitionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = _make_books(Path(self._tmp.name))

    def test_chapter_request_matches_parent_in_raw(self):
        # The miss that drove the daily re-download: chapter row, parent in raw/.
        m = find_local_book(
            "The Talent Code — Ch. 2: The Deep Practice Cell", "Daniel Coyle", books_root=self.root
        )
        self.assertIsNotNone(m)
        self.assertIn("TheTalentCode", m.path.name)

    def test_edition_request_matches_parent_in_raw(self):
        m = find_local_book("Do Hard Things — Ch. 11: Find Meaning", "Steve Magness", books_root=self.root)
        self.assertIsNotNone(m)
        self.assertIn("DoHardThings", m.path.name)

    def test_extracted_exact_title_is_recognized(self):
        # 80/20 Running lived recognizably in extracted/ (raw name was 8020).
        m = find_local_book("80/20 Running", "Matt Fitzgerald", books_root=self.root)
        self.assertIsNotNone(m)
        self.assertEqual(m.path.name, "80-20-running.txt")

    def test_extracted_does_not_subset_false_match(self):
        # "Peak" must NOT match extracted "peak-performance.txt" — different book.
        m = find_local_book("Peak", "Anders Ericsson", books_root=self.root)
        self.assertIsNone(m)

    def test_wrong_author_still_rejected(self):
        # Author guard on raw/ matches must survive request-canonicalization.
        m = find_local_book("The Talent Code — Ch. 2", "Some Other Author", books_root=self.root)
        self.assertIsNone(m)

    def test_absent_book_returns_none(self):
        m = find_local_book("Opening Up by Writing It Down", "James Pennebaker", books_root=self.root)
        self.assertIsNone(m)


if __name__ == "__main__":
    unittest.main()
