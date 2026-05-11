"""Tests for the research node's URL reachability classifier (Phase 3b).

No network. Mocks httpx via a tiny in-memory client adapter.

Run:  python -m unittest tests.test_research_node
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

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
        # Curator vouched yes, but URL is banned. Defense-in-depth flips to no.
        # Patch _classify_url not called when Reachable=yes — so we test the
        # banned-domain branch directly.
        rows = [
            {
                "Title": "x",
                "Source URL": "https://www.jstor.org/stable/123",
                "Reachable": "yes",
                "Reachability Reason": "originally vouched by curator",
            }
        ]

        def handler(req):
            return httpx.Response(200, text=GOOD_BODY)

        # Monkey-patch the module's httpx.Client builder is overkill; _verify_rows
        # opens its own client. Banned check runs first on Reachable=yes path,
        # so we don't need the client to fire at all.
        kept, dropped = _verify_rows(rows)
        self.assertEqual(kept[0]["Reachable"], "no")
        self.assertIn("banned-domain", kept[0]["Reachability Reason"])
        self.assertEqual(dropped, 1)


if __name__ == "__main__":
    unittest.main()
