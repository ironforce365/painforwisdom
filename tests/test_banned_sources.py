"""Tests for pipeline.banned_sources.

Run:  python -m unittest tests.test_banned_sources
or:   python tests/test_banned_sources.py
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.banned_sources import (  # noqa: E402
    BANNED_DOMAINS,
    banned_reason,
    is_banned,
)


class BannedSourcesTest(unittest.TestCase):
    def test_exact_domain_banned(self):
        self.assertTrue(is_banned("https://amazon.com/dp/0593138767"))
        self.assertTrue(is_banned("https://archive.org/details/something"))
        self.assertTrue(is_banned("https://jstor.org/stable/123"))

    def test_summary_sites_banned(self):
        self.assertTrue(is_banned("https://www.bookey.app/book/endure"))
        self.assertTrue(is_banned("https://www.blinkist.com/en/books/atomic-habits-en"))
        self.assertTrue(is_banned("https://www.getabstract.com/en/summary/123"))
        self.assertTrue(is_banned("https://www.shortform.com/book/comfort-crisis"))
        self.assertTrue(is_banned("https://www.12min.com/books/foo"))
        self.assertTrue(is_banned("https://fourminutebooks.com/endure-summary/"))

    def test_www_prefix_banned(self):
        self.assertTrue(is_banned("https://www.amazon.com/Comfort-Crisis/dp/0593138767"))
        self.assertTrue(is_banned("https://www.nytimes.com/2024/01/01/foo"))

    def test_subdomain_banned(self):
        self.assertTrue(is_banned("https://onlinelibrary.wiley.com/doi/10.1002/foo"))
        self.assertTrue(is_banned("https://link.springer.com/article/10.1007/foo"))

    def test_pmc_allowed_pubmed_banned(self):
        self.assertFalse(is_banned("https://pmc.ncbi.nlm.nih.gov/articles/PMC7381101/"))
        self.assertTrue(is_banned("https://pubmed.ncbi.nlm.nih.gov/23437923/"))

    def test_reachable_domains_allowed(self):
        self.assertFalse(is_banned("https://hubermanlab.com/episode/foo"))
        self.assertFalse(is_banned("https://en.wikipedia.org/wiki/Enchiridion_of_Epictetus"))
        self.assertFalse(is_banned("https://hiddenbrain.org/podcast/foo"))
        self.assertFalse(is_banned("https://jamesclear.com/atomic-habits"))

    def test_empty_or_malformed(self):
        self.assertFalse(is_banned(""))
        self.assertFalse(is_banned("not-a-url"))

    def test_banned_reason_returns_matched_domain(self):
        self.assertEqual(banned_reason("https://www.amazon.com/foo"), "amazon.com")
        self.assertEqual(
            banned_reason("https://onlinelibrary.wiley.com/doi/10.1002/foo"),
            "onlinelibrary.wiley.com",
        )
        self.assertIsNone(banned_reason("https://hubermanlab.com/foo"))

    def test_frozenset_immutable(self):
        with self.assertRaises(AttributeError):
            BANNED_DOMAINS.add("evil.com")  # type: ignore[attr-defined]


if __name__ == "__main__":
    unittest.main()
