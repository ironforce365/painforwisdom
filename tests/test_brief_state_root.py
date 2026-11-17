"""Daily brief must read and write state under STATE_ROOT, not PROJECT_ROOT.

Prod runs code from painforwisdom-live with PAINFORWISDOM_STATE_ROOT set to the
dev folder; in dev both roots are equal, so only split roots catch the bug.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.scripts import poc_brief_v2  # noqa: E402
from pipeline.summarize_daily import brief_writer  # noqa: E402
from pipeline.summarize_daily.clusterer import Cluster  # noqa: E402


class BriefSplitRootTests(unittest.TestCase):
    def setUp(self) -> None:
        state_tmp, code_tmp = TemporaryDirectory(), TemporaryDirectory()
        self.addCleanup(state_tmp.cleanup)
        self.addCleanup(code_tmp.cleanup)
        self.state = Path(state_tmp.name)
        self.code = Path(code_tmp.name)
        vault = self.state / "obsidian-vault"
        entries = vault / "gonzalo-book" / "entries"
        entries.mkdir(parents=True)
        (entries / "2026-11-01-anchor.md").write_text("vault body")
        self.llm_inputs: list = []

        def fake_llm(**kw):
            self.llm_inputs.append(kw["user_message"])
            return {"text": "stub"}

        for target, attr, value in (
            (brief_writer, "STATE_ROOT", self.state),
            (brief_writer, "CACHE_DIR", self.state / "briefs" / ".cache"),
            (brief_writer, "fetch_url", lambda url: "source text"),
            (poc_brief_v2, "PROJECT_ROOT", self.code),
            (poc_brief_v2, "STATE_ROOT", self.state),
            (poc_brief_v2, "VAULT_PATH", vault),
            (poc_brief_v2, "call_llm", fake_llm),
            (poc_brief_v2, "_commit_vault", lambda **kw: None),
        ):
            # create=True: if PROJECT_ROOT is ever re-imported, it stays split from STATE_ROOT.
            p = patch.object(target, attr, value, create=True)
            p.start()
            self.addCleanup(p.stop)

    def test_write_brief_returns_existing_dir_under_state_root(self):
        cluster = Cluster(
            theme="body-literacy",
            sub_angle="split-roots",
            vault_entry="2026-11-01-anchor",
            rows=[{"source_url": "https://example.com/a", "title": "A"}],
        )
        cluster_dir, skipped = brief_writer.write_brief(cluster)
        self.assertEqual(skipped, [])
        self.assertTrue(cluster_dir.is_dir(), cluster_dir)
        self.assertTrue((cluster_dir / "deep-dive.md").exists())
        self.assertTrue(cluster_dir.is_relative_to(self.state))
        self.assertFalse((self.code / "briefs").exists())
        self.assertTrue(any("vault body" in m for m in self.llm_inputs))


if __name__ == "__main__":
    unittest.main()
