"""Dry run: the whole pipeline on a real input, keeping nothing outside the run folder (spec 2026-11-06-pipeline-dry-run-design).

The nodes do not know about it. Before pipeline modules read their paths at import time, activate() points them at
<processed>/<run_id>/dry-run/ and at the sandbox Notion tables. Imports no pipeline module: runtime reads VAULT_PATH on import.
"""
from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path

FLAG = "PIPELINE_DRY_RUN"
VAULT_SOURCE = "PIPELINE_DRY_RUN_VAULT_SOURCE"
# Copies of pipeline.notion_client's defaults, which it cannot give: importing it reads the env. tests/test_dry_run.py compares them.
PROD_BLOG = "3185901b-efa9-8099-baac-000b2cb04d03"
PROD_RESEARCH = "dfd97a4e-0114-4cb8-8f75-658bb2b83b17"
_NOT_COPIED = shutil.ignore_patterns(".git", ".obsidian", ".trash")
_BOOK = "gonzalo-book"
_MENTIONS_ENTRY = ("_index.md", "book-outline.md", "themes/*.md", "frameworks/*.md")


class DryRunError(RuntimeError):
    pass


def active() -> bool:
    return bool(os.environ.get(FLAG))


def recorded(run_dir: Path) -> bool:
    """Was this run started as a dry run? Retries and the publisher go by this, not by how they were called."""
    try:
        return bool(json.loads((run_dir / "run.json").read_text()).get("dry_run"))
    except (OSError, ValueError):
        return False


def sandbox_notion(state_root: Path) -> dict[str, str]:
    """The key and table ids of .env.sandbox, or why not: a missing id would make notion_client use the real tables."""
    from dotenv import dotenv_values  # noqa: PLC0415 - pipeline.publish imports this module and must stay free of it

    path = state_root / ".env.sandbox"
    if not path.is_file():
        raise DryRunError(f"dry run needs the sandbox Notion tables, and there is no .env.sandbox in {state_root}")
    env = dotenv_values(path)
    names = ("NOTION_API_KEY", "NOTION_BLOG_DATA_SOURCE_ID", "NOTION_RESEARCH_DATA_SOURCE_ID")
    missing = [n for n in names if not (env.get(n) or "").strip()]
    if missing:
        raise DryRunError(f"dry run needs the sandbox Notion tables, and .env.sandbox has no {', '.join(missing)}")
    plain = lambda value: value.strip().replace("-", "").lower()  # noqa: E731
    for name, prod in ((names[1], PROD_BLOG), (names[2], PROD_RESEARCH)):
        if plain(env[name]) == plain(prod):
            raise DryRunError(f"dry run refuses to start: {name} in .env.sandbox is the real table")
    return {n: env[n].strip() for n in names}


def activate(run_dir: Path, state_root: Path) -> None:
    """Call after load_dotenv, before importing any pipeline module that reads these. run_dir need not exist."""
    notion = sandbox_notion(state_root)
    scratch = run_dir.parent / "dry-run"
    os.environ[VAULT_SOURCE] = os.environ.get("VAULT_PATH") or str(state_root / "obsidian-vault")
    os.environ.update(notion)
    os.environ.update({
        FLAG: "1",
        "VAULT_PATH": str(scratch / "vault"),
        "TRANSCRIPT_DIR": str(scratch / "transcripts"),
        "PAINFORWISDOM_THEMES_DB": str(scratch / "themes.db"),
    })


def prepare_vault(video_date: str) -> None:
    """Copy the vault for this run, once. Without the video's own day: the curator would answer ALREADY_PROCESSED or collide."""
    scratch, source = Path(os.environ["VAULT_PATH"]), Path(os.environ[VAULT_SOURCE])
    if scratch.exists():
        return  # a resumed run keeps what it already wrote
    shutil.copytree(source, scratch, ignore=_NOT_COPIED)
    book = scratch / _BOOK
    removed = sorted(book.glob(f"entries/{video_date}*.md"))
    for entry in removed:
        entry.unlink()
    if not removed:
        return
    mention = re.compile("|".join(re.escape(entry.stem) for entry in removed))
    for pattern in _MENTIONS_ENTRY:
        for path in book.glob(pattern):
            lines = path.read_text().splitlines(keepends=True)
            path.write_text("".join(line for line in lines if not mention.search(line)))
