"""Regression tests for the 2026-06-02 stale-reference incident.

Two distinct failure modes, one shared root pattern — a reference baked into
Notion that the live checkout can no longer resolve:

1. A book's `alt_source_url` was an ABSOLUTE `file://` path inside an ephemeral
   git worktree (`.../.claude/worktrees/end-of-may-backfill/books/extracted/...`).
   When that worktree was deleted, the daily-brief saw `local-file missing` and
   silently dropped the source — even though the identical extraction still
   lived under the canonical checkout's `books/extracted/`.

   Fixes pinned here:
     - `runtime.canonical_project_root()` strips a `.claude/worktrees/<name>`
       suffix so producers write + store paths rooted at the stable checkout.
     - `fetcher._fetch_local` re-anchors a missing absolute path's basename
       under the canonical `books/extracted/` before giving up (heals the
       already-baked dead rows).

2. A cluster's `vault_entry` slug (`2026-04-13-guilt-spiral-recovery`) never
   matched a real vault file — kb-curator had given the entry a different slug.
   `poc_brief_v2._read_vault` raised `FileNotFoundError`, aborting the WHOLE
   brief, even though every source fetched fine. The vault entry is only
   supplementary application-framing context; a stale anchor must degrade, not
   crash.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline import runtime  # noqa: E402
from pipeline.runtime import canonical_project_root  # noqa: E402
from pipeline.summarize_daily import fetcher  # noqa: E402
from pipeline.summarize_daily.fetcher import FetchError  # noqa: E402
from pipeline.scripts import poc_brief_v2  # noqa: E402


class CanonicalProjectRootTests(unittest.TestCase):
    def test_strips_worktree_suffix(self):
        wt = Path("/home/u/proj/.claude/worktrees/end-of-may-backfill")
        self.assertEqual(canonical_project_root(wt), Path("/home/u/proj"))

    def test_plain_checkout_unchanged(self):
        root = Path("/home/u/proj")
        self.assertEqual(canonical_project_root(root), Path("/home/u/proj"))

    def test_dotclaude_without_worktrees_unchanged(self):
        # A `.claude/agents` path is not a worktree — leave it alone.
        p = Path("/home/u/proj/.claude/agents")
        self.assertEqual(canonical_project_root(p), p)


class FetchLocalReanchorTests(unittest.TestCase):
    """A dead absolute worktree path whose basename still exists under the
    canonical `books/extracted/` must heal instead of raising."""

    def setUp(self) -> None:
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        (self.root / "books" / "extracted").mkdir(parents=True)
        self.live = self.root / "books" / "extracted" / "self-compassion.txt"
        self.live.write_text("EXTRACTED self-compassion text " * 50)
        p = patch.object(fetcher, "canonical_project_root", lambda: self.root)
        p.start()
        self.addCleanup(p.stop)

    def test_dead_worktree_path_reanchors(self):
        dead = (
            "file:///home/u/proj/.claude/worktrees/end-of-may-backfill/"
            "books/extracted/self-compassion.txt"
        )
        text = fetcher._fetch_local(dead)
        self.assertIn("EXTRACTED self-compassion text", text)

    def test_relative_path_resolved_against_canonical_root(self):
        text = fetcher._fetch_local("books/extracted/self-compassion.txt")
        self.assertIn("EXTRACTED self-compassion text", text)

    def test_genuinely_missing_still_raises(self):
        with self.assertRaises(FetchError):
            fetcher._fetch_local(
                "/home/u/proj/.claude/worktrees/x/books/extracted/not-a-real-book.txt"
            )


class ReadVaultGracefulDegradeTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.entries = self.root / "obsidian-vault" / "gonzalo-book" / "entries"
        self.entries.mkdir(parents=True)
        (self.entries / "2026-04-13-passion-as-high-performance.md").write_text(
            "real vault entry body"
        )
        p = patch.object(poc_brief_v2, "PROJECT_ROOT", self.root)
        p.start()
        self.addCleanup(p.stop)

    def test_existing_entry_returned(self):
        got = poc_brief_v2._read_vault("2026-04-13-passion-as-high-performance")
        self.assertEqual(got, "real vault entry body")

    def test_missing_entry_returns_empty_not_raises(self):
        got = poc_brief_v2._read_vault("2026-04-13-guilt-spiral-recovery")
        self.assertEqual(got, "")


if __name__ == "__main__":
    unittest.main()
