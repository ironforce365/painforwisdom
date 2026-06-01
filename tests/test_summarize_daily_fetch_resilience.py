"""Regression tests for the daily-summarizer fetch-failure handling.

Pins the behavior introduced after the 2026-05-13 silent-failure incident:

1. `config/fetch_denylist.txt` is honored — rows whose effective URL is on
   the list are dropped during `fetch_pending_rows` (never reach a cluster).
2. `brief_writer.cluster_to_v2_dict` catches per-row `FetchError`, returns
   `(dict, skipped)`, and continues with the survivors. A single 403 must
   not abort the run.
3. `brief_writer.write_brief` aborts loudly with `FetchError` when *every*
   row in the cluster fails (no silent zero-source brief).
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.summarize_daily import brief_writer, clusterer, fetcher  # noqa: E402
from pipeline.summarize_daily.brief_writer import cluster_to_v2_dict, write_brief  # noqa: E402
from pipeline.summarize_daily.clusterer import Cluster  # noqa: E402
from pipeline.summarize_daily.fetcher import FetchError  # noqa: E402


def _row(url: str, title: str = "Untitled") -> dict:
    return {
        "page_id": f"pid-{url}",
        "url": "https://notion.so/x",
        "title": title,
        "type": "Paper",
        "status": "To Read/Listen",
        "priority": "",
        "category": "",
        "coaching_theme": "t",
        "research_angle": "a",
        "author_host": "",
        "specific_location": "",
        "relevance": "",
        "source_url": url,
        "alt_source_url": "",
        "reachable": "yes",
        "vault_entry": "v",
    }


class DenyListTests(unittest.TestCase):
    def test_denylist_loaded_with_comments_and_blank_lines(self):
        with TemporaryDirectory() as td:
            denylist = Path(td) / "fetch_denylist.txt"
            denylist.write_text(
                "# this is a comment\n"
                "\n"
                "https://bad.example/page\n"
                "  https://trim.example/x  \n"
            )
            with patch.object(fetcher, "DENYLIST_FILE", denylist):
                self.assertTrue(fetcher.is_denylisted("https://bad.example/page"))
                self.assertTrue(fetcher.is_denylisted("https://trim.example/x"))
                self.assertFalse(fetcher.is_denylisted("https://ok.example/y"))
                self.assertFalse(fetcher.is_denylisted(""))

    def test_missing_denylist_file_treated_as_empty(self):
        with patch.object(fetcher, "DENYLIST_FILE", Path("/nonexistent/zzz.txt")):
            self.assertFalse(fetcher.is_denylisted("https://anything.example"))


class FetchPendingRowsTests(unittest.TestCase):
    def test_denylisted_rows_dropped_before_clustering(self):
        pages = [{"id": "p1"}, {"id": "p2"}]

        def fake_row_dict(page: dict) -> dict:
            base = _row("https://bad.example/x", title="Bad") if page["id"] == "p1" \
                else _row("https://good.example/x", title="Good")
            base["page_id"] = page["id"]
            return base

        with TemporaryDirectory() as td:
            denylist = Path(td) / "deny.txt"
            denylist.write_text("https://bad.example/x\n")
            with patch.object(clusterer, "query_research_tasks", return_value=iter(pages)), \
                    patch.object(clusterer, "_row_dict", side_effect=fake_row_dict), \
                    patch.object(fetcher, "DENYLIST_FILE", denylist):
                rows = clusterer.fetch_pending_rows()

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["page_id"], "p2")


class ClusterToV2Tests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        cache = Path(tmp.name) / "cache"
        p = patch.object(brief_writer, "CACHE_DIR", cache)
        p.start()
        self.addCleanup(p.stop)

    def _cluster(self, rows: list[dict]) -> Cluster:
        return Cluster(theme="t", sub_angle="a", vault_entry="v", rows=rows)

    def test_partial_failure_keeps_survivors_and_records_skipped(self):
        cluster = self._cluster([
            _row("https://good1.example"),
            _row("https://bad.example", title="Bad"),
            _row("https://good2.example"),
        ])

        def fake_fetch(url: str) -> str:
            if "bad" in url:
                raise FetchError("html http-status 403")
            return "extracted text " * 20

        with patch.object(brief_writer, "fetch_url", side_effect=fake_fetch):
            v2, skipped = cluster_to_v2_dict(cluster)

        self.assertEqual(
            [s["url"] for s in v2["sources"]],
            ["https://good1.example", "https://good2.example"],
        )
        self.assertEqual(len(skipped), 1)
        self.assertEqual(skipped[0]["url"], "https://bad.example")
        self.assertEqual(skipped[0]["title"], "Bad")
        self.assertIn("403", skipped[0]["error"])

    def test_all_rows_failing_returns_empty_sources_and_full_skip_list(self):
        cluster = self._cluster([
            _row("https://bad1.example"),
            _row("https://bad2.example"),
        ])
        with patch.object(brief_writer, "fetch_url", side_effect=FetchError("403")):
            v2, skipped = cluster_to_v2_dict(cluster)
        self.assertEqual(v2["sources"], [])
        self.assertEqual(len(skipped), 2)


class WriteBriefAbortsWhenAllFailTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        cache = Path(tmp.name) / "cache"
        p = patch.object(brief_writer, "CACHE_DIR", cache)
        p.start()
        self.addCleanup(p.stop)

    def test_zero_survivors_raises_fetcherror_and_skips_render(self):
        cluster = Cluster(
            theme="t",
            sub_angle="a",
            vault_entry="v",
            rows=[_row("https://bad1.example"), _row("https://bad2.example")],
        )
        with patch.object(brief_writer, "fetch_url", side_effect=FetchError("403")), \
                patch.object(brief_writer, "run_cluster") as mock_run:
            with self.assertRaises(FetchError) as ctx:
                write_brief(cluster)
        self.assertIn("no brief written", str(ctx.exception))
        mock_run.assert_not_called()


class EnsureSourceCachedBinaryHealTests(unittest.TestCase):
    """Pins the 2026-06-01 fix: a per-row cache holding raw binary (or its
    U+FFFD-corrupted text round-trip) must NOT be served straight to the focal
    `claude -p` pass. The short-circuit re-validates the cache head and falls
    through to a clean re-fetch when it sniffs as binary."""

    def setUp(self) -> None:
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.cache = Path(tmp.name) / "cache"
        self.cache.mkdir(parents=True)
        p = patch.object(brief_writer, "CACHE_DIR", self.cache)
        p.start()
        self.addCleanup(p.stop)

    def test_clean_cache_is_served_without_refetch(self):
        row = _row("https://example.com/a")
        slug = brief_writer._slug_for_source(row)
        (self.cache / f"{slug}.txt").write_text("clean research text " * 50)
        with patch.object(brief_writer, "fetch_url") as mock_fetch:
            got = brief_writer._ensure_source_cached(row)
        self.assertEqual(got, slug)
        mock_fetch.assert_not_called()

    def test_binary_cache_is_dropped_and_refetched(self):
        row = _row("https://example.com/book.mobi")
        slug = brief_writer._slug_for_source(row)
        # Simulate a stale cache written before the binary-extraction fix:
        # a MOBI header round-tripped through errors="replace".
        corrupt = "Some Book Title" + "\x00" * 20 + "BOOKMOBI" + "�" * 4000
        (self.cache / f"{slug}.txt").write_text(corrupt)
        with patch.object(brief_writer, "fetch_url", return_value="EXTRACTED clean text") as mock_fetch:
            got = brief_writer._ensure_source_cached(row)
        self.assertEqual(got, slug)
        mock_fetch.assert_called_once()
        self.assertEqual((self.cache / f"{slug}.txt").read_text(), "EXTRACTED clean text")


if __name__ == "__main__":
    unittest.main()
