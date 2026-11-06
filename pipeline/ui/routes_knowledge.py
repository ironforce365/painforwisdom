"""Knowledge base routes (spec 2026-11-06-console-dashboards)."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request

from pipeline.ui import knowledge

router = APIRouter()


@router.get("/api/knowledge")
def get_knowledge(request: Request, limit: Optional[int] = Query(None, ge=1)) -> dict:
    return knowledge.overview(knowledge.vault_for(request.app.state.settings), limit)


@router.get("/api/knowledge/{slug}")
def get_entry(request: Request, slug: str) -> dict:
    text = knowledge.entry(knowledge.vault_for(request.app.state.settings), slug)
    if text is None:
        raise HTTPException(404, "unknown entry")
    return {"slug": slug, "markdown": text}
