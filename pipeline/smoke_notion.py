"""Smoke test: verify NOTION_API_KEY works and both target databases have
the property names our notion_client expects.

Does NOT create any pages. Just lists database properties and reports diffs
against the expected schema. Run before the first live pipeline run to
catch property-name mismatches without burning a full run.

Usage:
    python -m pipeline.smoke_notion
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Dict, Set

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

from pipeline.notion_client import (  # noqa: E402
    BLOG_DATA_SOURCE_ID,
    RESEARCH_DATA_SOURCE_ID,
    get_client,
)


EXPECTED_BLOG_PROPS = {"Title", "Date", "Published?"}
EXPECTED_RESEARCH_PROPS = {
    "Title",
    "Type",
    "Status",
    "Priority",
    "Author/Host",
    "Specific Location",
    "Relevance",
    "Research Angle",
    "Category",
    "Source URL",
    "Paywall",
    "Vault Entry",
    "Coaching Theme",
}


def _list_data_source_props(data_source_id: str) -> Dict[str, str]:
    """Return {prop_name: prop_type} for a Notion data source."""
    client = get_client()
    # In modern Notion API, retrieve a data source via /v1/data_sources/{id}
    # python-notion-client exposes .data_sources.retrieve in v3+.
    if hasattr(client, "data_sources"):
        ds = client.data_sources.retrieve(data_source_id=data_source_id)
    else:
        # Fallback for older SDK: data_source_id often equals the database id.
        ds = client.databases.retrieve(database_id=data_source_id)
    props = ds.get("properties", {}) or {}
    return {name: spec.get("type", "?") for name, spec in props.items()}


def _diff(label: str, actual: Dict[str, str], expected: Set[str]) -> int:
    print(f"\n## {label}")
    actual_names = set(actual.keys())
    missing = expected - actual_names
    extra = actual_names - expected
    print(f"Properties found ({len(actual_names)}):")
    for name in sorted(actual_names):
        marker = "✓" if name in expected else " "
        print(f"  {marker} {name}: {actual[name]}")
    if missing:
        print(f"\n✗ MISSING (we expect, DB does not have): {sorted(missing)}")
    if extra:
        print(f"  (extra in DB, harmless): {sorted(extra)}")
    return len(missing)


def main() -> int:
    if not os.getenv("NOTION_API_KEY"):
        print("✗ NOTION_API_KEY not set. Add it to .env first.")
        return 2

    issues = 0
    try:
        blog_props = _list_data_source_props(BLOG_DATA_SOURCE_ID)
        issues += _diff("Blog post pending publications", blog_props, EXPECTED_BLOG_PROPS)
    except Exception as exc:
        print(f"✗ Failed to fetch blog DB: {exc}")
        issues += 1

    try:
        research_props = _list_data_source_props(RESEARCH_DATA_SOURCE_ID)
        issues += _diff("Research Tasks", research_props, EXPECTED_RESEARCH_PROPS)
    except Exception as exc:
        print(f"✗ Failed to fetch research DB: {exc}")
        issues += 1

    print()
    if issues == 0:
        print("✓ Smoke test PASS — schemas match, ready for live run.")
        return 0
    print(f"✗ {issues} issue(s) — fix before live run.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
