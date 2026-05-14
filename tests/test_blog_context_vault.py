"""Unit tests for the vault-backed cross-post context backend."""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from textwrap import dedent

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _write_entry(entries_dir: Path, slug: str, date: str, themes: list[str], body: str) -> Path:
    entries_dir.mkdir(parents=True, exist_ok=True)
    path = entries_dir / f"{slug}.md"
    theme_links = ", ".join(f"[[{t}]]" for t in themes)
    path.write_text(
        dedent(
            f"""\
            # {date} — {slug.replace('-', ' ').title()}

            **Date:** {date}
            **Themes:** {theme_links}
            **Frameworks:**

            ## Core Insight
            {body}
            """
        )
    )
    return path


class VaultBackendTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        root = Path(self.tmpdir.name) / "gonzalo-book"
        entries = root / "entries"
        _write_entry(
            entries,
            "2026-04-09-the-cookie-jar",
            "2026-04-09",
            ["resilience-toolkit"],
            "The cookie jar is Goggins' frame for storing hard-won wins.",
        )
        _write_entry(
            entries,
            "2026-04-12-amcc-effect",
            "2026-04-12",
            ["deliberate-discomfort", "agency"],
            "Voluntary hard choices build the aMCC override muscle.",
        )
        _write_entry(
            entries,
            "2026-04-15-comparison-trap",
            "2026-04-15",
            ["resilience-toolkit"],
            "External comparison is a rigged game.",
        )
        os.environ["VAULT_PATH"] = str(Path(self.tmpdir.name))
        # Reset the class-level cache so each test loads fresh entries.
        from pipeline.blog_context.vault_backend import VaultBackend
        VaultBackend.invalidate_cache()

    def test_find_references_matches_theme_slug(self) -> None:
        from pipeline.blog_context.vault_backend import VaultBackend
        backend = VaultBackend()
        refs = backend.find_references("resilience-toolkit", limit=5)
        self.assertEqual(len(refs), 2)
        # Newest entry first.
        self.assertEqual(refs[0].slug, "2026-04-15-comparison-trap")

    def test_find_references_substring_match(self) -> None:
        from pipeline.blog_context.vault_backend import VaultBackend
        backend = VaultBackend()
        refs = backend.find_references("cookie jar", limit=5)
        self.assertEqual(len(refs), 1)
        self.assertIn("cookie", refs[0].snippet.lower())

    def test_recent_topics_aggregates_theme_counts(self) -> None:
        from pipeline.blog_context.vault_backend import VaultBackend
        backend = VaultBackend()
        topics = backend.recent_topics(limit=10)
        names = {t.name: t.count for t in topics}
        self.assertEqual(names.get("resilience-toolkit"), 2)
        self.assertEqual(names.get("deliberate-discomfort"), 1)

    def test_recent_topics_records_last_seen(self) -> None:
        from pipeline.blog_context.vault_backend import VaultBackend
        backend = VaultBackend()
        topics = backend.recent_topics(limit=10)
        for t in topics:
            if t.name == "resilience-toolkit":
                self.assertEqual(t.last_seen, "2026-04-15")
                return
        self.fail("resilience-toolkit topic not found")


if __name__ == "__main__":
    unittest.main(verbosity=2)
