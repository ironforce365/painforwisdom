"""Inbox file → Notion task. Idempotent: marks promoted files by renaming `.md` → `.promoted.md`."""
from __future__ import annotations
from pathlib import Path
import os

from notion_client import Client


def _notion_create_page(**kwargs) -> dict:
    notion = Client(auth=os.environ["NOTION_API_KEY"])
    return notion.pages.create(**kwargs)


def _classify_top(text: str) -> list[str]:
    from sidecar.classify_themes import classify
    return [m.theme for m in classify(text, top_n=3)]


def promote(inbox_root: Path, data_source_id: str) -> list[Path]:
    promoted: list[Path] = []
    for f in sorted(inbox_root.rglob("*.md")):
        if f.name.endswith(".promoted.md"):
            continue
        text = f.read_text(encoding="utf-8")
        theme_hints = _classify_top(text)
        _notion_create_page(
            parent={"data_source_id": data_source_id},
            properties={
                "title": [{"text": {"content": f"Coach inbox: {f.stem}"}}],
            },
            children=[
                {"object": "block", "type": "paragraph", "paragraph": {
                    "rich_text": [{"type": "text", "text": {"content": f"Theme hints: {', '.join(theme_hints)}"}}]
                }},
                {"object": "block", "type": "code", "code": {
                    "rich_text": [{"type": "text", "text": {"content": text[:1900]}}],
                    "language": "markdown",
                }},
            ],
        )
        new = f.with_suffix(".promoted.md")
        f.rename(new)
        promoted.append(new)
    return promoted
