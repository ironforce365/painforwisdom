"""Tests for the research node's URL reachability classifier (Phase 3b).

No network. Mocks httpx via a tiny in-memory client adapter.

Run:  python -m unittest tests.test_research_node
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402

from pipeline.nodes.research import _classify_url, _verify_rows  # noqa: E402


def _mock_transport(handler):
    return httpx.MockTransport(handler)


def _make_client(handler):
    return httpx.Client(transport=_mock_transport(handler), follow_redirects=True)


GOOD_BODY = (
    "<html><body>"
    + ("Mechanism of effortful behaviour: anterior mid-cingulate cortex computes "
       "allostatic prediction. " * 50)
    + "</body></html>"
)
PAYWALL_BODY = "<html><body>Subscribers only — sign in to read.</body></html>"
THIN_BODY = "<html><body>tiny</body></html>"


class ClassifyUrlTest(unittest.TestCase):
    def test_reachable_url_passes(self):
        def handler(req):
            return httpx.Response(200, text=GOOD_BODY)

        with _make_client(handler) as client:
            state, reason = _classify_url("https://pmc.ncbi.nlm.nih.gov/articles/PMC7381101/", client)
        self.assertEqual(state, "yes")
        self.assertIn("verified", reason)

    def test_404_marked_unreachable(self):
        def handler(req):
            return httpx.Response(404)

        with _make_client(handler) as client:
            state, reason = _classify_url("https://example.com/missing", client)
        self.assertEqual(state, "no")
        self.assertIn("404", reason)

    def test_paywall_phrase_in_body_rejected(self):
        def handler(req):
            return httpx.Response(200, text=PAYWALL_BODY)

        with _make_client(handler) as client:
            state, reason = _classify_url("https://newssite.example/article", client)
        self.assertEqual(state, "no")
        self.assertIn("paywall", reason)

    def test_thin_extract_rejected(self):
        def handler(req):
            return httpx.Response(200, text=THIN_BODY)

        with _make_client(handler) as client:
            state, reason = _classify_url("https://thin.example/page", client)
        self.assertEqual(state, "no")
        self.assertIn("thin-extract", reason)

    def test_banned_domain_rejected_before_fetch(self):
        called = {"n": 0}

        def handler(req):
            called["n"] += 1
            return httpx.Response(200, text=GOOD_BODY)

        with _make_client(handler) as client:
            state, reason = _classify_url("https://www.amazon.com/dp/123", client)
        self.assertEqual(state, "no")
        self.assertIn("banned-domain", reason)
        self.assertEqual(called["n"], 0)  # Never fetched

    def test_pubmed_banned_pmc_allowed(self):
        def handler(req):
            return httpx.Response(200, text=GOOD_BODY)

        with _make_client(handler) as client:
            pubmed_state, _ = _classify_url(
                "https://pubmed.ncbi.nlm.nih.gov/23437923/", client
            )
            pmc_state, _ = _classify_url(
                "https://pmc.ncbi.nlm.nih.gov/articles/PMC7381101/", client
            )
        self.assertEqual(pubmed_state, "no")
        self.assertEqual(pmc_state, "yes")


class VerifyRowsTest(unittest.TestCase):
    def test_curator_yes_with_banned_url_gets_overridden(self):
        rows = [
            {
                "Title": "x",
                "Type": "Paper",
                "Source URL": "https://www.jstor.org/stable/123",
                "Reachable": "yes",
                "Reachability Reason": "originally vouched by curator",
            }
        ]
        kept, dropped = _verify_rows(rows)
        self.assertEqual(kept[0]["Reachable"], "no")
        self.assertIn("banned-domain", kept[0]["Reachability Reason"])
        self.assertEqual(dropped, 1)

    def test_curator_yes_with_bookey_url_gets_overridden(self):
        # The specific bug that triggered this fix — bookey.app returns 403 to
        # bots; curator vouched yes; banned list now catches it.
        rows = [
            {
                "Title": "Endure",
                "Type": "Book",
                "Author/Host": "Some Unknown Author",  # won't match local
                "Source URL": "https://www.bookey.app/book/endure",
                "Reachable": "yes",
                "Reachability Reason": "vouched",
            }
        ]
        kept, dropped = _verify_rows(rows)
        self.assertEqual(kept[0]["Reachable"], "no")
        self.assertIn("banned-domain", kept[0]["Reachability Reason"])
        self.assertEqual(dropped, 1)

    def test_curator_yes_404_gets_overridden_by_http_fetch(self):
        # Curator vouched yes for a URL that 404s. New behavior: full HTTP
        # verify catches this even though URL is not on the banned list.
        rows = [
            {
                "Title": "x",
                "Type": "Article",
                "Source URL": "https://example.com/dead-link",
                "Reachable": "yes",
                "Reachability Reason": "vouched by curator",
            }
        ]

        def fake_classify(url, client):
            return "no", "http-status: 404"

        with patch("pipeline.nodes.research._classify_url", side_effect=fake_classify):
            kept, dropped = _verify_rows(rows)
        self.assertEqual(kept[0]["Reachable"], "no")
        self.assertIn("OVERRIDDEN", kept[0]["Reachability Reason"])
        self.assertIn("404", kept[0]["Reachability Reason"])
        self.assertEqual(dropped, 1)

    def test_curator_no_is_preserved_without_refetch(self):
        rows = [
            {
                "Title": "x",
                "Type": "Paper",
                "Source URL": "https://paywalled.example/foo",
                "Reachable": "no",
                "Reachability Reason": "paywall (deliberate)",
            }
        ]

        called = {"n": 0}

        def fake_classify(url, client):
            called["n"] += 1
            return "yes", "verified 9999 chars"

        with patch("pipeline.nodes.research._classify_url", side_effect=fake_classify):
            kept, dropped = _verify_rows(rows)
        self.assertEqual(kept[0]["Reachable"], "no")
        self.assertEqual(called["n"], 0)
        self.assertEqual(dropped, 1)

    def test_local_book_match_overrides_web_url(self):
        # When a Book row matches the local inventory, _verify_rows rewrites
        # the Source URL to file:// and skips HTTP verify entirely.
        from pipeline.local_books import LocalBook

        rows = [
            {
                "Title": "Endure",
                "Type": "Book",
                "Author/Host": "Alex Hutchinson",
                "Source URL": "https://www.bookey.app/book/endure",
                "Reachable": "yes",
                "Reachability Reason": "vouched",
            }
        ]
        fake_path = Path("/home/x/books/raw/HutchinsonAlex_Endure_2018_engli_1.epub")
        fake_match = LocalBook(
            path=fake_path, display_title="Endure", display_author="Hutchinson Alex"
        )

        called = {"http": 0}

        def fake_classify(url, client):
            called["http"] += 1
            return "yes", "verified"

        with patch("pipeline.nodes.research.find_local_book", return_value=fake_match):
            with patch(
                "pipeline.nodes.research._classify_url", side_effect=fake_classify
            ):
                kept, dropped = _verify_rows(rows)

        self.assertEqual(kept[0]["Source URL"], f"file://{fake_path}")
        self.assertEqual(kept[0]["Reachable"], "yes")
        self.assertIn("local-curated", kept[0]["Reachability Reason"])
        self.assertEqual(called["http"], 0)
        self.assertEqual(dropped, 0)

    def test_non_book_row_is_not_local_matched(self):
        # Type=Paper should never trigger local lookup even if title matches.
        rows = [
            {
                "Title": "Endure",
                "Type": "Paper",
                "Author/Host": "Alex Hutchinson",
                "Source URL": "https://pmc.ncbi.nlm.nih.gov/articles/PMC1/",
            }
        ]

        def fake_classify(url, client):
            return "yes", "verified"

        with patch(
            "pipeline.nodes.research.find_local_book", return_value=None
        ) as mock_find:
            with patch(
                "pipeline.nodes.research._classify_url", side_effect=fake_classify
            ):
                _verify_rows(rows)
        mock_find.assert_not_called()


if __name__ == "__main__":
    unittest.main()
