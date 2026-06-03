"""Regression tests for guarded download retry (2026-06-02).

A flaky upstream 5xx on `download_book` used to permanently lose a book for the
run (downloads weren't retried). `_bridge_call_download` retries ONCE on
transient patterns only — never on not-found / quota-exceeded / extract-failed,
to avoid burning the daily download quota.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline import zlibrary_bridge as zb  # noqa: E402


class DownloadRetryTests(unittest.TestCase):
    def setUp(self) -> None:
        # No real sleeping between retries.
        p = patch.object(zb.time, "sleep", lambda *_: None)
        p.start()
        self.addCleanup(p.stop)

    def test_transient_then_success_retries_once(self):
        seq = [
            (1, "", "httpx... Server error '500 Internal Server Error'"),
            (0, '{"ok": true}', ""),
        ]
        calls = []

        def fake(function, args):
            calls.append(function)
            return seq[len(calls) - 1]

        with patch.object(zb, "_bridge_call", side_effect=fake):
            rc, out, err = zb._bridge_call_download({"book_details": {}})
        self.assertEqual(rc, 0)
        self.assertEqual(calls, ["download_book", "download_book"])

    def test_persistent_transient_gives_up_after_one_retry(self):
        calls = []

        def fake(function, args):
            calls.append(function)
            return 1, "", "ReadTimeout"

        with patch.object(zb, "_bridge_call", side_effect=fake):
            rc, out, err = zb._bridge_call_download({"book_details": {}})
        self.assertEqual(rc, 1)
        self.assertEqual(len(calls), 2, "one retry, then give up")

    def test_non_transient_is_not_retried(self):
        # not-found / quota / extract failures must not burn a second attempt.
        for stderr in ("No results found", "daily download limit reached", "extract failed"):
            calls = []

            def fake(function, args):
                calls.append(function)
                return 1, "", stderr

            with patch.object(zb, "_bridge_call", side_effect=fake):
                rc, out, err = zb._bridge_call_download({"book_details": {}})
            self.assertEqual(rc, 1)
            self.assertEqual(len(calls), 1, f"must not retry on: {stderr!r}")


if __name__ == "__main__":
    unittest.main()
