"""Graph node names vs runs.jsonl stages, artifact dirs and icons (spec 2026-10-31-pipeline-console-ui §3)."""
from __future__ import annotations

from typing import Optional

NODE_TO_STAGE: dict[str, Optional[str]] = {
    "transcribe": "transcribe",
    "extract": "extract",
    "kb_curator": "kb-curator",
    "writer": "writer",
    "research": "research",
    "notion_blog": "notion-blog",
    "notion_research": "notion-research",
    "extract_image": "extract-image",
    "youtube_upload": "youtube-upload",
    "wordpress_draft": "wordpress-draft",
    "summarizer": "summarizer",
    "pre_validator": None,
    "bv_research": None,
    "bv_wordpress": None,
    "bv_youtube": None,
}

# 2026-05 runs logged one `validator` row per validator fire.
LEGACY_STAGE_ALIASES: dict[str, str] = {"validator": "summarizer"}

NODE_TO_DIR: dict[str, tuple[str, ...]] = {
    "transcribe": (),
    "extract": ("coaching-thought-extractor",),
    "kb_curator": ("kb-curator",),
    "writer": ("painforwisdom-writer",),
    "research": ("research-curator",),
    "notion_blog": ("notion-blog-post-logger",),
    "notion_research": ("notion-research-logger",),
    "extract_image": ("wordpress-draft",),
    "youtube_upload": ("youtube-upload",),
    "wordpress_draft": ("wordpress-draft",),
    "summarizer": ("validator", "pipeline-summary"),
    "pre_validator": (),
    "bv_research": (),
    "bv_wordpress": (),
    "bv_youtube": (),
}

NODE_ICON: dict[str, str] = {
    "transcribe": "mic",
    "extract": "spark",
    "kb_curator": "book",
    "writer": "pen",
    "research": "search",
    "notion_blog": "notion",
    "notion_research": "notion",
    "extract_image": "image",
    "youtube_upload": "video",
    "wordpress_draft": "globe",
    "summarizer": "flag",
    "pre_validator": "check",
    "bv_research": "check",
    "bv_wordpress": "check",
    "bv_youtube": "check",
}

_STAGE_TO_NODE = {stage: node for node, stage in NODE_TO_STAGE.items() if stage}

# wordpress-draft is shared with extract_image; the files there are the draft's.
DIR_TO_NODE: dict[str, str] = {d: n for n, dirs in NODE_TO_DIR.items() for d in dirs if n != "extract_image"}


def node_for_stage(stage: str) -> Optional[str]:
    return _STAGE_TO_NODE.get(LEGACY_STAGE_ALIASES.get(stage, stage))
