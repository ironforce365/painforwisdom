"""C9 field specs per target. Standard library only: the node modules import clients the UI server must not import."""
from __future__ import annotations

from typing import Optional

YOUTUBE_TITLE_MAX = 100
YOUTUBE_TAGS_MAX_CHARS = 500


def field_spec(type_: str, label: str, max_: Optional[int] = None, options: Optional[list[str]] = None,
               required: bool = False) -> dict:
    return {"type": type_, "label": label, "max": max_, "options": options, "required": required}


WORDPRESS = {
    "title": field_spec("string", "Title", 200, required=True),
    "body": field_spec("markdown", "Body", required=True),
    "excerpt": field_spec("text", "Excerpt", 300),
    "tags": field_spec("tags", "Tags"),
    "categories": field_spec("tags", "Categories"),
    "status": field_spec("enum", "Status", options=["draft", "publish"]),
    "youtube_url": field_spec("url", "YouTube URL"),
}

YOUTUBE = {
    "title": field_spec("string", "Title", YOUTUBE_TITLE_MAX, required=True),
    "description": field_spec("text", "Description", 5000),
    "tags": field_spec("tags", "Tags", YOUTUBE_TAGS_MAX_CHARS),
    "privacy_status": field_spec("enum", "Privacy", options=["private", "unlisted", "public"]),
    "kind": field_spec("enum", "Kind", options=["short", "video"]),
}

FIELD_SPECS = {"wordpress": WORDPRESS, "youtube": YOUTUBE}
