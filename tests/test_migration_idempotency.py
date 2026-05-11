"""Regression guard: after migration applied, a second --dry-run must show
no planned changes. Verifies idempotency of pipeline.scripts.migrate_notion_schema.

Skipped automatically when NOTION_API_KEY is not present (offline / CI without
secret), so unit-test suite stays runnable everywhere.

Run:  python -m unittest tests.test_migration_idempotency
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
except ImportError:
    pass


@unittest.skipUnless(os.getenv("NOTION_API_KEY"), "NOTION_API_KEY not set")
class MigrationIdempotencyTest(unittest.TestCase):
    def test_no_planned_changes_after_apply(self):
        from pipeline.scripts.migrate_notion_schema import (
            _build_property_patch,
            _get_current_schema,
        )

        current = _get_current_schema()
        patch = _build_property_patch(current)

        self.assertEqual(
            patch,
            {},
            f"Migration should be idempotent post-apply; got planned changes: {list(patch)}",
        )

    def test_all_new_properties_exist(self):
        from pipeline.scripts.migrate_notion_schema import (
            NEW_PROPERTIES,
            _get_current_schema,
        )

        current = _get_current_schema()
        missing = [name for name, _, _ in NEW_PROPERTIES if name not in current]
        self.assertEqual(missing, [], f"Expected properties missing post-apply: {missing}")

    def test_status_has_summarized_option(self):
        from pipeline.scripts.migrate_notion_schema import _get_current_schema

        current = _get_current_schema()
        status = current.get("Status", {})
        opts = {o.get("name") for o in status.get("select", {}).get("options", [])}
        self.assertIn("Summarized", opts)


if __name__ == "__main__":
    unittest.main()
