"""C3 run actions, log and research routes (spec 2026-11-05-console-end-to-end section 3)."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from pipeline.ui import actions, research, runs

router = APIRouter()


class ReplyBody(BaseModel):
    id: str
    reply: str


class AutoApproveBody(BaseModel):
    auto_approve: bool = False


class DismissBody(BaseModel):
    dismissed: bool = True


def _error(e: actions.ActionError) -> JSONResponse:
    return JSONResponse(e.body, status_code=e.status)


@router.post("/api/runs/{run_id}/reply", status_code=202)
def post_reply(request: Request, run_id: str, body: ReplyBody):
    try:
        actions.reply(request.app.state.settings, run_id, body.id, body.reply)
    except actions.ActionError as e:
        return _error(e)
    return {"accepted": True}


@router.post("/api/runs/{run_id}/stop", status_code=202)
def post_stop(request: Request, run_id: str):
    try:
        actions.stop(request.app.state.settings, run_id)
    except actions.ActionError as e:
        return _error(e)
    return {"accepted": True}


@router.post("/api/runs/{run_id}/retry", status_code=202)
def post_retry(request: Request, run_id: str, body: AutoApproveBody = AutoApproveBody()):
    try:
        actions.retry(request.app.state.settings, run_id, body.auto_approve)
    except actions.ActionError as e:
        return _error(e)
    return {"run_id": run_id}


@router.post("/api/runs/{run_id}/restart", status_code=201)
def post_restart(request: Request, run_id: str, body: AutoApproveBody = AutoApproveBody()):
    try:
        return {"run_id": actions.restart(request.app.state.settings, run_id, body.auto_approve)}
    except actions.ActionError as e:
        return _error(e)


@router.post("/api/runs/{run_id}/dismiss")
def post_dismiss(request: Request, run_id: str, body: DismissBody = DismissBody()):
    try:
        actions.dismiss(request.app.state.settings, run_id, body.dismissed)
    except actions.ActionError as e:
        return _error(e)
    return {"dismissed": body.dismissed}


@router.delete("/api/runs/{run_id}")
def delete_run(request: Request, run_id: str):
    try:
        actions.delete_run(request.app.state.settings, run_id)
    except actions.ActionError as e:
        return _error(e)
    return {"deleted": run_id}


@router.get("/api/runs/{run_id}/log")
def get_log(request: Request, run_id: str, tail: int = Query(500, ge=1, le=20_000)):
    try:
        return actions.log_tail(request.app.state.settings, run_id, tail)
    except actions.ActionError as e:
        return _error(e)


@router.get("/api/runs/{run_id}/research")
def get_research(request: Request, run_id: str) -> dict:
    settings = request.app.state.settings
    rd = runs.run_dir(settings.processed_root, run_id)
    if rd is None:
        raise HTTPException(404, "unknown run")
    return {"items": research.items(rd, settings.books_root)}


@router.get("/api/runs/{run_id}/research/{n}/file")
def get_research_file(request: Request, run_id: str, n: int) -> FileResponse:
    settings = request.app.state.settings
    rd = runs.run_dir(settings.processed_root, run_id)
    path = research.local_file(rd, settings.books_root, n) if rd is not None else None
    if path is None:
        raise HTTPException(404, "not a local item")
    return FileResponse(path, media_type=research.media_type(path))
