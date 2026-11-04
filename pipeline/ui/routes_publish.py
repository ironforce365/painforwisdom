"""C3 publish routes (spec 2026-11-03-console-operability §1.2)."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from pipeline.ui import publish, runs

router = APIRouter()


class PublishBody(BaseModel):
    fields: dict[str, Any]


@router.get("/api/runs/{run_id}/publish")
def get_publish(request: Request, run_id: str) -> dict:
    rd = runs.run_dir(request.app.state.settings.processed_root, run_id)
    if rd is None:
        raise HTTPException(404, "unknown run")
    return publish.list_packages(rd)


@router.post("/api/runs/{run_id}/publish/{target}", status_code=202)
def post_publish(request: Request, run_id: str, target: str, body: PublishBody):
    try:
        return publish.confirm(request.app.state.settings, run_id, target, body.fields)
    except publish.PublishError as e:
        return JSONResponse(e.body, status_code=e.status)
