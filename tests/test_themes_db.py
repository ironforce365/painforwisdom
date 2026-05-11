"""Tests for pipeline.themes_db and the seed_themes_db script.

Run:  python -m unittest tests.test_themes_db
or:   python tests/test_themes_db.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline import themes_db  # noqa: E402
from pipeline.themes_db import Theme  # noqa: E402


class _TempDB:
    """Context manager that points themes_db at a fresh temp file."""

    def __enter__(self):
        self._dir = tempfile.TemporaryDirectory()
        self._path = Path(self._dir.name) / "themes.db"
        os.environ["PAINFORWISDOM_THEMES_DB"] = str(self._path)
        return self._path

    def __exit__(self, *exc):
        os.environ.pop("PAINFORWISDOM_THEMES_DB", None)
        self._dir.cleanup()


def _sample_themes() -> list[Theme]:
    return [
        Theme("umbrella-a", None, "dead", "umbrella-a", "DEAD umbrella A", 999, "(dead)"),
        Theme("sub-a1", "umbrella-a", "active", "umbrella-a", "first sub of A", 10, "rule a1"),
        Theme("sub-a2", "umbrella-a", "active", "umbrella-a", "second sub of A", 11, "rule a2"),
        Theme("solo", None, "active", "solo", "standalone terminal", 1, "rule solo"),
    ]


class SchemaAndLookupTest(unittest.TestCase):
    def test_connect_creates_schema(self):
        with _TempDB() as path:
            conn = themes_db.connect(auto_seed=False)
            try:
                self.assertTrue(path.exists())
                rows = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
                names = {r[0] for r in rows}
                self.assertIn("themes", names)
            finally:
                conn.close()

    def test_upsert_and_get(self):
        with _TempDB():
            conn = themes_db.connect(auto_seed=False)
            try:
                themes_db.upsert_many(conn, _sample_themes())
                got = themes_db.get(conn, "sub-a1")
                self.assertIsNotNone(got)
                assert got is not None
                self.assertEqual(got.parent, "umbrella-a")
                self.assertEqual(got.status, "active")
            finally:
                conn.close()

    def test_get_agent_routes_subtheme_to_parent(self):
        with _TempDB():
            conn = themes_db.connect(auto_seed=False)
            try:
                themes_db.upsert_many(conn, _sample_themes())
                self.assertEqual(themes_db.get_agent(conn, "sub-a1"), "umbrella-a")
                self.assertEqual(themes_db.get_agent(conn, "sub-a2"), "umbrella-a")
                self.assertEqual(themes_db.get_agent(conn, "solo"), "solo")
                self.assertIsNone(themes_db.get_agent(conn, "nope"))
            finally:
                conn.close()

    def test_list_active_excludes_dead(self):
        with _TempDB():
            conn = themes_db.connect(auto_seed=False)
            try:
                themes_db.upsert_many(conn, _sample_themes())
                active_names = {t.name for t in themes_db.list_active(conn)}
                dead_names = {t.name for t in themes_db.list_dead(conn)}
                self.assertEqual(active_names, {"sub-a1", "sub-a2", "solo"})
                self.assertEqual(dead_names, {"umbrella-a"})
            finally:
                conn.close()

    def test_list_children(self):
        with _TempDB():
            conn = themes_db.connect(auto_seed=False)
            try:
                themes_db.upsert_many(conn, _sample_themes())
                children = themes_db.list_children(conn, "umbrella-a")
                names = [c.name for c in children]
                self.assertEqual(names, ["sub-a1", "sub-a2"])  # sorted by priority
            finally:
                conn.close()

    def test_upsert_is_idempotent(self):
        with _TempDB():
            conn = themes_db.connect(auto_seed=False)
            try:
                themes_db.upsert_many(conn, _sample_themes())
                themes_db.upsert_many(conn, _sample_themes())
                count = conn.execute("SELECT COUNT(*) FROM themes").fetchone()[0]
                self.assertEqual(count, 4)
            finally:
                conn.close()

    def test_upsert_updates_existing_row(self):
        with _TempDB():
            conn = themes_db.connect(auto_seed=False)
            try:
                themes_db.upsert_many(conn, _sample_themes())
                themes_db.upsert(
                    conn,
                    Theme(
                        "solo", None, "active", "solo", "updated def", 7, "updated rule"
                    ),
                )
                conn.commit()
                got = themes_db.get(conn, "solo")
                assert got is not None
                self.assertEqual(got.definition, "updated def")
                self.assertEqual(got.priority, 7)
            finally:
                conn.close()


class SeedSmokeTest(unittest.TestCase):
    """End-to-end smoke for the seeder + canonical agent lookups."""

    def test_seed_then_lookup_canonical_themes(self):
        with _TempDB():
            from pipeline.scripts.seed_themes_db import main as seed_main

            rc = seed_main([])
            self.assertEqual(rc, 0)

            conn = themes_db.connect(auto_seed=False)
            try:
                # Sub-themes route to umbrella agents
                self.assertEqual(
                    themes_db.get_agent(conn, "heat-and-physical-hardship-protocols"),
                    "deliberate-discomfort",
                )
                self.assertEqual(
                    themes_db.get_agent(conn, "comfort-creep-and-self-deception"),
                    "comfort-as-default",
                )
                # Terminal top-level
                self.assertEqual(themes_db.get_agent(conn, "amcc-effect"), "amcc-effect")
                # Unknown
                self.assertIsNone(themes_db.get_agent(conn, "this-theme-does-not-exist"))
                # Dead umbrellas exist but are flagged
                self.assertEqual(
                    {t.name for t in themes_db.list_dead(conn)},
                    {"comfort-as-default", "deliberate-discomfort"},
                )
            finally:
                conn.close()


if __name__ == "__main__":
    unittest.main()
