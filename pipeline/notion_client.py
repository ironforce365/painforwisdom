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
from typing import Any, Dict, Iterator, List, Optional

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
_BLOG_SCHEMA_CACHE: Optional[set[str]] = None


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


def get_blog_db_properties(force_refresh: bool = False) -> set[str]:
    """Return the set of property names currently defined on the blog DB.

    Cached for the life of the process. Used to defensively drop optional
    properties (``Status``, ``Excerpt``, ``WordPress URL``) when the F6
    schema migration has not been applied yet so we never send a payload
    Notion will reject.
    """
    global _BLOG_SCHEMA_CACHE
    if _BLOG_SCHEMA_CACHE is None or force_refresh:
        try:
            resp = get_client().data_sources.retrieve(data_source_id=BLOG_DATA_SOURCE_ID)
            _BLOG_SCHEMA_CACHE = set((resp.get("properties") or {}).keys())
        except Exception as exc:  # noqa: BLE001
            # Fall back to the known-required minimum; better to attempt
            # a Title/Date/Published-only create than to crash early.
            print(f"[notion] WARN failed to retrieve blog DB schema: {exc}")
            _BLOG_SCHEMA_CACHE = {"Title", "Date", "Published?"}
    return _BLOG_SCHEMA_CACHE


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
    status: Optional[str] = None,
    excerpt: Optional[str] = None,
) -> Dict[str, Any]:
    """Create a 'Blog post pending publications' page. Returns full page response.

    ``status`` and ``excerpt`` map to the F6 schema additions (Status select,
    Excerpt rich_text). They are silently ignored when the migration has not
    been applied yet (the API rejects unknown properties — we don't fight it).
    """
    client = get_client()
    children = text_to_blocks(body_text)
    available = get_blog_db_properties()
    properties: Dict[str, Any] = {
        "Title": {"title": [{"text": {"content": title}}]},
        "Date": {"date": {"start": video_date}},
        "Published?": {"checkbox": published},
    }
    if status and "Status" in available:
        properties["Status"] = {"select": {"name": status}}
    elif status:
        print(
            "[notion] WARN blog DB has no 'Status' property — skipping. "
            "Run: python -m pipeline.scripts.migrate_notion_blog_schema --apply"
        )
    if excerpt and "Excerpt" in available:
        properties["Excerpt"] = {"rich_text": [{"text": {"content": excerpt[:1900]}}]}
    elif excerpt:
        print(
            "[notion] WARN blog DB has no 'Excerpt' property — skipping. "
            "Run: python -m pipeline.scripts.migrate_notion_blog_schema --apply"
        )
    page = client.pages.create(
        parent={"type": "data_source_id", "data_source_id": BLOG_DATA_SOURCE_ID},
        properties=properties,
        children=children,
    )
    time.sleep(_PACE_SECONDS)
    return page


def update_blog_page_wordpress_url(
    page_id_str: str,
    wordpress_url: str,
    *,
    status: Optional[str] = "Draft Created",
    excerpt: Optional[str] = None,
) -> Dict[str, Any]:
    """Patch a blog page after the WordPress draft is created.

    Silently drops properties the DB does not yet have (so this is safe to
    call before the F6 schema migration has been applied).
    """
    client = get_client()
    available = get_blog_db_properties()
    properties: Dict[str, Any] = {}
    if wordpress_url and "WordPress URL" in available:
        properties["WordPress URL"] = {"url": wordpress_url}
    if status and "Status" in available:
        properties["Status"] = {"select": {"name": status}}
    if excerpt and "Excerpt" in available:
        properties["Excerpt"] = {"rich_text": [{"text": {"content": excerpt[:1900]}}]}
    if not properties:
        print(
            "[notion] WARN update_blog_page_wordpress_url: no matching properties "
            "available on blog DB — run the schema migration. Skipping update."
        )
        return {}
    page = client.pages.update(page_id=page_id_str, properties=properties)
    time.sleep(_PACE_SECONDS)
    return page


def query_blog_pages(
    *,
    only_unpublished: bool = True,
    sort_date_asc: bool = True,
    page_size: int = 100,
    extra_filter: Optional[Dict[str, Any]] = None,
) -> Iterator[Dict[str, Any]]:
    """Paginated query of the Blog post pending publications data source.

    Default behaviour returns rows with ``Published? = false``, sorted by
    ``Date`` ascending — the chronological order required for backfill so
    later posts can safely reference earlier ones.
    """
    client = get_client()
    cursor: Optional[str] = None
    filters: List[Dict[str, Any]] = []
    if only_unpublished:
        filters.append({"property": "Published?", "checkbox": {"equals": False}})
    if extra_filter:
        filters.append(extra_filter)
    combined_filter: Optional[Dict[str, Any]]
    if not filters:
        combined_filter = None
    elif len(filters) == 1:
        combined_filter = filters[0]
    else:
        combined_filter = {"and": filters}

    sorts = [{"property": "Date", "direction": "ascending" if sort_date_asc else "descending"}]
    while True:
        params: Dict[str, Any] = {
            "data_source_id": BLOG_DATA_SOURCE_ID,
            "page_size": page_size,
            "sorts": sorts,
        }
        if combined_filter:
            params["filter"] = combined_filter
        if cursor:
            params["start_cursor"] = cursor
        resp = client.data_sources.query(**params)
        for page in resp.get("results", []):
            yield page
        if not resp.get("has_more"):
            return
        cursor = resp.get("next_cursor")
        time.sleep(_PACE_SECONDS)


def get_blog_page(page_id_str: str) -> Dict[str, Any]:
    """Retrieve a single blog page by ID."""
    client = get_client()
    return client.pages.retrieve(page_id=page_id_str)


def get_blog_page_markdown(page_id_str: str) -> str:
    """Round-trip a blog page's paragraph blocks into a markdown body.

    The pipeline only ever writes paragraph blocks for blog posts, so we
    only need to handle paragraphs + headings + dividers here. Anything
    else degrades to plain text.
    """
    blocks = fetch_page_blocks(page_id_str)
    lines: List[str] = []
    for block in blocks:
        btype = block.get("type")
        if btype == "paragraph":
            text = "".join(
                t.get("plain_text", "")
                for t in block.get("paragraph", {}).get("rich_text", [])
            )
            lines.append(text)
            lines.append("")
        elif btype == "heading_2":
            text = "".join(
                t.get("plain_text", "")
                for t in block.get("heading_2", {}).get("rich_text", [])
            )
            lines.append(f"## {text}")
            lines.append("")
        elif btype == "heading_3":
            text = "".join(
                t.get("plain_text", "")
                for t in block.get("heading_3", {}).get("rich_text", [])
            )
            lines.append(f"### {text}")
            lines.append("")
        elif btype == "divider":
            lines.append("---")
            lines.append("")
        else:
            inner = block.get(btype or "", {}) if btype else {}
            if not isinstance(inner, dict):
                inner = {}
            text = "".join(
                t.get("plain_text", "")
                for t in inner.get("rich_text", [])
            )
            if text:
                lines.append(text)
                lines.append("")
    return "\n".join(lines).strip() + "\n"


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
    reachable: str = "yes",
    reachability_reason: str = "",
    alt_source_url: str = "",
) -> Dict[str, Any]:
    """Create a 'Research Tasks' page. Caller assembles body blocks via the
    helpers above (heading_2 / paragraph / divider).

    Phase 2a added Reachable, Reachability Reason, Alt Source URL. They default
    to yes / empty / empty so older call sites stay green; Phase 3b fills them.
    """
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
    if reachable in ("yes", "no", "unknown"):
        properties["Reachable"] = {"select": {"name": reachable}}
    if reachability_reason:
        properties["Reachability Reason"] = {
            "rich_text": [{"text": {"content": reachability_reason[:1900]}}]
        }
    if alt_source_url:
        properties["Alt Source URL"] = {"url": alt_source_url}

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


def query_research_tasks(
    *,
    filter: Optional[Dict[str, Any]] = None,
    page_size: int = 100,
) -> Iterator[Dict[str, Any]]:
    """Paginated query of the Research Tasks data source.

    Yields raw Notion page dicts. Use `extract_property` to read values out of
    `page["properties"]`. No filter = every row in the DB.
    """
    client = get_client()
    cursor: Optional[str] = None
    while True:
        params: Dict[str, Any] = {
            "data_source_id": RESEARCH_DATA_SOURCE_ID,
            "page_size": page_size,
        }
        if filter:
            params["filter"] = filter
        if cursor:
            params["start_cursor"] = cursor
        resp = client.data_sources.query(**params)
        for page in resp.get("results", []):
            yield page
        if not resp.get("has_more"):
            return
        cursor = resp.get("next_cursor")
        time.sleep(_PACE_SECONDS)


def extract_property(page: Dict[str, Any], name: str) -> Any:
    """Pull a typed scalar out of a Notion page's properties dict.

    Returns "" for missing/empty values. Booleans for checkboxes. Strings for
    title/rich_text/select/url. None for unknown types — caller decides.
    """
    prop = page.get("properties", {}).get(name)
    if prop is None:
        return ""
    ptype = prop.get("type")
    if ptype == "title":
        return "".join(t.get("plain_text", "") for t in prop.get("title", []))
    if ptype == "rich_text":
        return "".join(t.get("plain_text", "") for t in prop.get("rich_text", []))
    if ptype == "select":
        sel = prop.get("select")
        return sel.get("name", "") if sel else ""
    if ptype == "multi_select":
        return [s.get("name", "") for s in prop.get("multi_select", [])]
    if ptype == "url":
        return prop.get("url") or ""
    if ptype == "checkbox":
        return bool(prop.get("checkbox", False))
    if ptype == "date":
        d = prop.get("date")
        return d.get("start", "") if d else ""
    return None


def fetch_theme_research_tasks(theme: str) -> List[Dict[str, Any]]:
    """Stateful research-curator helper: fetch all Research Tasks for a theme.

    Returns flat dicts (NOT raw Notion pages) keyed by the field names the
    research-curator agent reads. Used to seed the curator's "already-covered"
    set so it does NOT propose duplicate references during web search.

    Filter is Coaching Theme equality on the rich_text property. Returns rows
    of any Status (To Read/Listen, Summarized, Archived-Duplicate) because the
    curator should treat all of them as "already in vault on this theme."
    """
    filter_obj = {"property": "Coaching Theme", "rich_text": {"equals": theme}}
    rows: List[Dict[str, Any]] = []
    for page in query_research_tasks(filter=filter_obj, page_size=100):
        rows.append({
            "page_id": page.get("id", ""),
            "title": extract_property(page, "Title"),
            "type": extract_property(page, "Type"),
            "status": extract_property(page, "Status"),
            "author_host": extract_property(page, "Author/Host") or "",
            "specific_location": extract_property(page, "Specific Location") or "",
            "research_angle": extract_property(page, "Research Angle") or "",
            "source_url": extract_property(page, "Source URL") or "",
            "alt_source_url": extract_property(page, "Alt Source URL") or "",
            "vault_entry": extract_property(page, "Vault Entry") or "",
            "category": extract_property(page, "Category") or "",
            "relevance": extract_property(page, "Relevance") or "",
        })
    return rows


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
