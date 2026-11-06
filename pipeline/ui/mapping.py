"""Graph node names vs runs.jsonl stages, artifact dirs and icons (spec 2026-10-31-pipeline-console-ui §3)."""
from __future__ import annotations

from typing import Optional

NODE_LABEL: dict[str, str] = {
    "transcribe": "Transcribe",
    "extract": "Extract",
    "extract_image": "Pick image",
    "kb_curator": "Knowledge base",
    "pre_validator": "Check",
    "writer": "Write blog",
    "research": "Research",
    "notion_blog": "Blog to Notion",
    "youtube_metadata": "YouTube text",
    "notion_research": "Research to Notion",
    "wordpress_draft": "WordPress",
    "youtube_upload": "YouTube",
    "bv_research": "Check research",
    "bv_wordpress": "Check WordPress",
    "bv_youtube": "Check YouTube",
    "summarizer": "Summary",
}

NODE_TO_STAGE: dict[str, Optional[str]] = {
    "transcribe": "transcribe",
    "extract": "extract",
    "kb_curator": "kb-curator",
    "writer": "writer",
    "research": "research",
    "notion_blog": "notion-blog",
    "notion_research": "notion-research",
    "extract_image": "extract-image",
    "youtube_metadata": "youtube-metadata",
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
    "youtube_metadata": ("youtube-upload",),
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
    "youtube_metadata": "tag",
    "youtube_upload": "video",
    "wordpress_draft": "globe",
    "summarizer": "flag",
    "pre_validator": "check",
    "bv_research": "check",
    "bv_wordpress": "check",
    "bv_youtube": "check",
}

_STAGE_TO_NODE = {stage: node for node, stage in NODE_TO_STAGE.items() if stage}

# wordpress-draft and youtube-upload are each shared by two nodes; files no `end.outputs` claims go to the later node.
DIR_TO_NODE: dict[str, str] = {d: n for n, dirs in NODE_TO_DIR.items() for d in dirs
                               if n not in {"extract_image", "youtube_metadata"}}


def node_for_stage(stage: str) -> Optional[str]:
    return _STAGE_TO_NODE.get(LEGACY_STAGE_ALIASES.get(stage, stage))
