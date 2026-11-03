"""Run result read path (spec 2026-10-31-pipeline-console-ui §4). pipeline.notion_client is imported lazily: it reads env at import."""
from __future__ import annotations

import importlib
import json
from pathlib import Path
from typing import Any, Optional, Protocol

from dotenv import dotenv_values

from pipeline import runtime
from pipeline.ui.runs import ACTIVE
from pipeline.ui.settings import Settings

REASON_CAP = 200


class ResultSource(Protocol):
    def blog(self, page_id: str) -> dict: ...

    def research(self, page_ids: list[str]) -> list[dict]: ...


class NotionResultSource:
    def __init__(self, env_file: Optional[Path] = None) -> None:
        self.env_file = env_file

    def _nc(self) -> Any:
        nc = importlib.import_module("pipeline.notion_client")
        # Key from the profile's env file, never os.environ: spawned runs inherit the server env (spec §5.2, §10).
        key = dotenv_values(self.env_file).get("NOTION_API_KEY") if self.env_file else None
        if key:
            nc.get_client(token=key)
        return nc

    def blog(self, page_id: str) -> dict:
        nc = self._nc()
        page = nc.get_blog_page(page_id)
        return {"title": nc.extract_property(page, "Title"), "url": nc.page_url(page),
                "markdown": nc.get_blog_page_markdown(page_id)}

    def research(self, page_ids: list[str]) -> list[dict]:
        nc = self._nc()
        client = nc.get_client()
        out = []
        for pid in page_ids:
            page = client.pages.retrieve(page_id=pid)
            out.append({"title": nc.extract_property(page, "Title"), "type": nc.extract_property(page, "Type"),
                        "url": nc.extract_property(page, "Source URL") or nc.page_url(page)})
        return out


class FixtureResultSource:
    def __init__(self, root: Path) -> None:
        self.root = root

    def blog(self, page_id: str) -> dict:
        return json.loads((self.root / "blog" / f"{Path(page_id).name}.json").read_text())

    def research(self, page_ids: list[str]) -> list[dict]:
        return [json.loads((self.root / "research" / f"{Path(p).name}.json").read_text()) for p in page_ids]


def source_for(settings: Settings) -> ResultSource:
    if settings.notion_fixtures:
        return FixtureResultSource(settings.notion_fixtures)
    # .env lives in STATE_ROOT, not the tag-pinned live checkout (io-ab244036).
    return NotionResultSource(runtime.STATE_ROOT / (".env.sandbox" if settings.profile == "sandbox" else ".env"))


def _payload(status: str, reason: Optional[str] = None, blog: Optional[dict] = None,
             research: Optional[list] = None) -> dict:
    return {"status": status, "reason": reason, "blog": blog, "research": research}


def _last_row(rows: list[dict], stage: str) -> Optional[dict]:
    return next((r for r in reversed(rows) if r.get("stage") == stage), None)


def resolve(status: Optional[str], rows: list[dict], source: ResultSource) -> dict:
    """C3 result from a run's status and runs.jsonl rows; never raises on a source error."""
    blog_row, research_row = _last_row(rows, "notion-blog"), _last_row(rows, "notion-research")
    if status in ACTIVE and (blog_row is None or research_row is None):
        return _payload("pending")
    if blog_row is None or not blog_row.get("page_id"):
        return _payload("unavailable", "no blog page recorded for this run")
    ids = research_row.get("page_ids") if research_row else None
    try:
        blog = source.blog(str(blog_row["page_id"]))
        research = source.research([str(i) for i in ids]) if isinstance(ids, list) else None
    except Exception as exc:  # any source failure is a reason, never a 5xx (spec §4.3)
        return _payload("unavailable", f"{type(exc).__name__}: {exc}"[:REASON_CAP])
    return _payload("ready", blog=blog, research=research)
