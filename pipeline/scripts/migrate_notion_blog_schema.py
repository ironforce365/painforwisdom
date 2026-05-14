"""Idempotent Notion schema migration for the Blog post pending publications data source.

Adds these properties (only if missing):
  - WordPress URL  url
  - Status         select (Pending / Draft Created / Scheduled / Published)
  - Excerpt        rich_text

The legacy ``Published?`` checkbox stays in place. The first backfill touch on
a row with ``Published? = true`` flips ``Status`` to ``Published``.

Safe to re-run. Checks current schema first; only patches the deltas.

Usage:
  python -m pipeline.scripts.migrate_notion_blog_schema --dry-run
  python -m pipeline.scripts.migrate_notion_blog_schema --apply
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Dict

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

from pipeline.notion_client import BLOG_DATA_SOURCE_ID, get_client  # noqa: E402


NEW_PROPERTIES: list[tuple[str, str, Dict[str, Any]]] = [
    ("WordPress URL", "url", {"url": {}}),
    (
        "Status",
        "select",
        {
            "select": {
                "options": [
                    {"name": "Pending", "color": "gray"},
                    {"name": "Draft Created", "color": "blue"},
                    {"name": "Scheduled", "color": "yellow"},
                    {"name": "Published", "color": "green"},
                ]
            }
        },
    ),
    ("Excerpt", "rich_text", {"rich_text": {}}),
]


def _get_current_schema() -> Dict[str, Dict[str, Any]]:
    client = get_client()
    resp = client.data_sources.retrieve(data_source_id=BLOG_DATA_SOURCE_ID)
    return resp.get("properties") or {}


def _build_property_patch(current: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    patch: Dict[str, Dict[str, Any]] = {}
    for name, ptype, cfg in NEW_PROPERTIES:
        if name not in current:
            patch[name] = cfg
            continue
        existing_type = current[name].get("type")
        if existing_type != ptype:
            print(
                f"  ! {name!r}: type mismatch (existing={existing_type!r}, "
                f"target={ptype!r}). Skipping — manual reconciliation required."
            )
            continue
        # For a Status select that already exists, ensure required options are present.
        if ptype == "select":
            existing_opts = {
                o.get("name") for o in current[name].get("select", {}).get("options", [])
            }
            target_opts = cfg["select"]["options"]
            missing = [o for o in target_opts if o["name"] not in existing_opts]
            if missing:
                full = list(current[name].get("select", {}).get("options", []))
                full.extend(missing)
                patch[name] = {"select": {"options": full}}
    return patch


def _summarise(patch: Dict[str, Dict[str, Any]]) -> None:
    if not patch:
        print("Schema is already up-to-date. Nothing to migrate.")
        return
    print("Planned property changes:")
    for name, cfg in patch.items():
        ptype = next(iter(cfg))
        if ptype == "select":
            opts = [o["name"] for o in cfg["select"]["options"]]
            print(f"  + {name}  ({ptype})  options={opts}")
        else:
            print(f"  + {name}  ({ptype})")


def _apply(patch: Dict[str, Dict[str, Any]]) -> None:
    client = get_client()
    client.data_sources.update(data_source_id=BLOG_DATA_SOURCE_ID, properties=patch)
    print(f"Applied {len(patch)} property change(s).")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    g = parser.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true", help="Print plan, do not write.")
    g.add_argument("--apply", action="store_true", help="Apply the migration.")
    args = parser.parse_args(argv)

    print(f"Reading current schema for blog data source {BLOG_DATA_SOURCE_ID}...")
    current = _get_current_schema()
    print(f"Found {len(current)} existing properties.")

    patch = _build_property_patch(current)
    _summarise(patch)

    if args.dry_run or not patch:
        return 0

    _apply(patch)
    print("Re-fetching schema to verify...")
    after = _get_current_schema()
    for name, _, _ in NEW_PROPERTIES:
        present = name in after
        print(f"  {'✓' if present else '✗'} {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
