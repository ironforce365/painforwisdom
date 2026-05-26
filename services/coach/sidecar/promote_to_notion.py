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
    try:
        return [m.theme for m in classify(text, top_n=3)]
    except FileNotFoundError:
        # theme embedding cache not built yet — promote without theme hints
        return []


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


def main() -> int:
    """Cron entrypoint: walk COACH_INBOX_ROOT, promote each unread inbox file to Notion."""
    import logging
    log = logging.getLogger("coach.promote")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    inbox = Path(os.environ.get("COACH_INBOX_ROOT", "/vault/_inbox"))
    data_source_id = os.environ.get("NOTION_COACH_INBOX_DATA_SOURCE_ID")
    if not data_source_id:
        log.error("NOTION_COACH_INBOX_DATA_SOURCE_ID not set; aborting")
        return 1
    if not inbox.exists():
        log.info("inbox %s does not exist; nothing to promote", inbox)
        return 0
    promoted = promote(inbox_root=inbox, data_source_id=data_source_id)
    log.info("promoted %d files", len(promoted))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
