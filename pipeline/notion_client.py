"""Direct Notion REST wrapper for the two Notion stages.

The Paperclip-era agents used the `mcp__claude_ai_Notion__*` MCP tools, which
authenticate via the Claude desktop app's Notion connector. LangGraph nodes
run as plain Python and can't invoke MCP tools mid-graph, so we hit the
Notion REST API directly via the official `notion-client` SDK.

This requires a separate Notion **internal integration token** (different
from the MCP/claude.ai auth). Set it as `NOTION_API_KEY` in `.env` and share
both target databases with the integration in the Notion UI.

Database IDs (lifted from the legacy agent prompts):
  - Blog post pending publications: 3185901befa9800489d2dcd03fdb5ec8
  - Research Tasks:                 64b70c23f694412895b72a383001c0f2

Data source IDs (modern Notion API needs these for create_pages):
  - Blog DB:     3185901b-efa9-8099-baac-000b2cb04d03
  - Research DB: dfd97a4e-0114-4cb8-8f75-658bb2b83b17
"""
from __future__ import annotations

import os
import time
from typing import Any, Dict, List, Optional

from notion_client import Client


# Production data source IDs are baked in as fallbacks so existing prod runs
# keep working without env changes. Sandbox profile overrides via env vars
# pointing at separate Notion DBs.
_DEFAULT_BLOG_DATA_SOURCE_ID = "3185901b-efa9-8099-baac-000b2cb04d03"
_DEFAULT_RESEARCH_DATA_SOURCE_ID = "dfd97a4e-0114-4cb8-8f75-658bb2b83b17"

BLOG_DATA_SOURCE_ID = os.environ.get("NOTION_BLOG_DATA_SOURCE_ID") or _DEFAULT_BLOG_DATA_SOURCE_ID
RESEARCH_DATA_SOURCE_ID = os.environ.get("NOTION_RESEARCH_DATA_SOURCE_ID") or _DEFAULT_RESEARCH_DATA_SOURCE_ID

# Pacing — Notion's documented rate limit is ~3 req/s.
_PACE_SECONDS = 0.4


_CLIENT: Optional[Client] = None


def get_client() -> Client:
    global _CLIENT
    if _CLIENT is None:
        token = os.getenv("NOTION_API_KEY")
        if not token:
            raise RuntimeError(
                "NOTION_API_KEY not set. Create a Notion internal integration "
                "(https://www.notion.so/profile/integrations), add the token to .env, "
                "and share the Blog + Research databases with the integration."
            )
        _CLIENT = Client(auth=token)
    return _CLIENT


def _paragraph(text: str) -> Dict[str, Any]:
    return {
        "object": "block",
        "type": "paragraph",
        "paragraph": {"rich_text": [{"type": "text", "text": {"content": text}}]},
    }


def _heading_2(text: str) -> Dict[str, Any]:
    return {
        "object": "block",
        "type": "heading_2",
        "heading_2": {"rich_text": [{"type": "text", "text": {"content": text}}]},
    }


def _divider() -> Dict[str, Any]:
    return {"object": "block", "type": "divider", "divider": {}}


def text_to_blocks(text: str) -> List[Dict[str, Any]]:
    """Split a multi-paragraph string into Notion paragraph blocks.
    Notion paragraph blocks have a 2000-char rich-text limit; we chunk longer
    paragraphs to stay safe.
    """
    blocks: List[Dict[str, Any]] = []
    for para in text.split("\n\n"):
        para = para.strip()
        if not para:
            continue
        # Chunk long paragraphs at 1900 chars (paragraph rich_text limit ~2000).
        for i in range(0, len(para), 1900):
            blocks.append(_paragraph(para[i : i + 1900]))
    return blocks


def create_blog_page(
    title: str,
    body_text: str,
    video_date: str,
    *,
    published: bool = False,
) -> Dict[str, Any]:
    """Create a 'Blog post pending publications' page. Returns full page response."""
    client = get_client()
    children = text_to_blocks(body_text)
    page = client.pages.create(
        parent={"type": "data_source_id", "data_source_id": BLOG_DATA_SOURCE_ID},
        properties={
            "Title": {"title": [{"text": {"content": title}}]},
            "Date": {"date": {"start": video_date}},
            "Published?": {"checkbox": published},
        },
        children=children,
    )
    time.sleep(_PACE_SECONDS)
    return page


def create_research_task(
    *,
    title: str,
    ref_type: str,
    priority: str,
    author_host: str,
    specific_location: str,
    relevance: str,
    research_angle: str,
    category: str,
    source_url: str,
    paywall: bool,
    coaching_theme: str,
    vault_entry: str,
    body_blocks: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Create a 'Research Tasks' page. Caller assembles body blocks via the
    helpers above (heading_2 / paragraph / divider)."""
    client = get_client()
    properties: Dict[str, Any] = {
        "Title": {"title": [{"text": {"content": title}}]},
        "Type": {"select": {"name": ref_type}},
        "Status": {"select": {"name": "To Read/Listen"}},
        "Priority": {"select": {"name": priority}},
        "Author/Host": {"rich_text": [{"text": {"content": author_host}}]},
        "Specific Location": {"rich_text": [{"text": {"content": specific_location}}]},
        "Relevance": {"rich_text": [{"text": {"content": relevance}}]},
        "Research Angle": {"rich_text": [{"text": {"content": research_angle}}]},
        "Category": {"select": {"name": category}},
        "Paywall": {"checkbox": paywall},
        "Vault Entry": {"rich_text": [{"text": {"content": vault_entry}}]},
        "Coaching Theme": {"rich_text": [{"text": {"content": coaching_theme}}]},
    }
    if source_url:
        properties["Source URL"] = {"url": source_url}

    page = client.pages.create(
        parent={"type": "data_source_id", "data_source_id": RESEARCH_DATA_SOURCE_ID},
        properties=properties,
        children=body_blocks,
    )
    time.sleep(_PACE_SECONDS)
    return page


def page_url(page: Dict[str, Any]) -> str:
    return page.get("url", "") or ""


def page_id(page: Dict[str, Any]) -> str:
    return page.get("id", "") or ""


def fetch_page_blocks(page_id_str: str) -> List[Dict[str, Any]]:
    """Used by validator to fetch back a created page and confirm body non-empty."""
    client = get_client()
    blocks: List[Dict[str, Any]] = []
    cursor: Optional[str] = None
    while True:
        params: Dict[str, Any] = {"block_id": page_id_str, "page_size": 50}
        if cursor:
            params["start_cursor"] = cursor
        resp = client.blocks.children.list(**params)
        blocks.extend(resp.get("results", []))
        if not resp.get("has_more"):
            break
        cursor = resp.get("next_cursor")
    return blocks
