"""Shared LangGraph state for the 7-stage content pipeline.

LangGraph merges dicts via reducers; we use total=False so individual nodes
can return partial state without breaking. Lists use operator.add reducer
where appended to in parallel branches (metrics)."""
from __future__ import annotations

from operator import add
from typing import Annotated, Any, Dict, List, Optional, TypedDict


class State(TypedDict, total=False):
    # Inputs
    video_path: str
    run_id: str
    run_dir: str
    video_date: str

    # Stage 1 — transcribe
    transcript_path: str
    transcript_text: str
    transcript_word_count: int

    # Stage 2 — extract
    extraction_report: str
    extraction_report_path: str
    content_quality: str  # Strong / Weak / Flagged
    core_insight: str
    blog_post_seed: str
    story_anchor: str
    themes: List[str]
    frameworks: List[str]

    # Stage 3 — kb-curator
    vault_entry_path: str
    vault_entry_slug: str
    curator_summary_path: str
    themes_attached: List[str]
    frameworks_attached: List[str]
    pending_approval: Optional[Dict[str, Any]]  # {"kind": "theme"|"framework", "name": ..., "reason": ...}

    # Stage 4a — writer
    blog_post_path: str
    blog_post_title: str
    blog_post_text: str
    blog_post_excerpt: str  # 50-word summary parsed from writer output

    # Stage 4b — research
    research_csv_path: str
    research_count: int

    # Stage 4c — extract_image (parallel branch from extract)
    featured_image_path: str
    featured_image_score: float
    image_extraction_failed: bool

    # Stage 4d — youtube_upload (parallel branch from extract)
    youtube_video_id: str
    youtube_url: str
    youtube_skipped: bool
    youtube_skip_reason: str

    # Stage 5a — notion-blog
    notion_blog_url: str
    notion_blog_page_id: str
    notion_blog_summary_path: str

    # Stage 5b — notion-research
    notion_task_count: int
    notion_research_summary_path: str

    # Stage 5c — wordpress_draft (joins notion_blog + extract_image)
    wordpress_post_id: Optional[int]
    wordpress_url: str
    wordpress_dormant: bool
    wordpress_skipped: bool
    wordpress_skip_reason: str
    wordpress_bundle_path: str

    # Stage 6 — validator
    validator_verdict: str  # PASS | PARTIAL | FAIL
    validator_report_path: str
    pipeline_summary_path: str

    # Telemetry — appended in parallel-safe way
    metrics: Annotated[List[Dict[str, Any]], add]
