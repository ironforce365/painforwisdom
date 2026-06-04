"""Post-turn hook: persist each (user, assistant) exchange as a markdown file
under vault/_inbox/<user_id>/<timestamp>.md for later HITL curation."""
from __future__ import annotations
from datetime import datetime, timezone
from pathlib import Path


def write_inbox_entry(
    *,
    inbox_root: Path,
    user_id: str,
    user_text: str,
    assistant_text: str,
    retrieved_sources: list[str],
) -> Path:
    user_dir = inbox_root / user_id
    user_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = user_dir / f"{ts}.md"
    path.write_text(
        f"""---
user_id: {user_id}
timestamp: {ts}
retrieved_sources: {retrieved_sources}
---

## User

{user_text}

## Coach

{assistant_text}
""",
        encoding="utf-8",
    )
    return path
