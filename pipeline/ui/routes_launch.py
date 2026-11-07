"""C3 inputs and launch routes (spec 2026-10-31-pipeline-console-ui §5)."""
from __future__ import annotations

import uuid
from typing import Literal, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from pipeline.ui import launcher

router = APIRouter()


class FakeSpec(BaseModel):
    scenario: Literal["ok", "hitl", "fail", "error", "error_twice", "non_recoverable", "curator"] = "ok"
    step_ms: int = Field(200, ge=0, le=60_000)
    wait_s: int = Field(120, ge=1, le=3600)


class LaunchBody(BaseModel):
    input: str
    auto_approve: bool = False
    language: Optional[str] = None
    dry_run: bool = False
    fake: Optional[FakeSpec] = None


@router.get("/api/inputs")
def list_inputs(request: Request) -> dict:
    settings = request.app.state.settings
    return {"inbox": str(settings.inbox.resolve()), "default_language": launcher.default_language(settings),
            "inputs": launcher.list_inputs(settings.inbox, settings.processed_root)}


@router.put("/api/inputs/{name}", status_code=201)
async def put_input(request: Request, name: str) -> dict:
    """Raw-body upload into the inbox; no python-multipart (spec Conventions: no new runtime deps)."""
    inbox = request.app.state.settings.inbox
    try:
        target = launcher.upload_target(inbox, name)
    except launcher.LaunchError as e:
        raise HTTPException(e.status, e.detail) from e
    if target.exists():
        raise HTTPException(409, "input exists")
    inbox.mkdir(parents=True, exist_ok=True)
    part = inbox / f".{name}.{uuid.uuid4().hex}.part"  # dot prefix: hidden from list_inputs while streaming
    try:
        with open(part, "wb") as f:
            async for chunk in request.stream():
                f.write(chunk)
    except BaseException:
        part.unlink(missing_ok=True)
        raise
    try:
        launcher.publish_upload(part, target)
    except launcher.LaunchError as e:
        raise HTTPException(e.status, e.detail) from e
    st = target.stat()
    return {"name": name, "kind": launcher.input_kind(name), "bytes": st.st_size, "mtime": st.st_mtime}


@router.post("/api/runs", status_code=201)
def post_run(request: Request, body: LaunchBody):
    fake = body.fake.model_dump() if body.fake is not None else None
    try:
        run_id = launcher.launch(request.app.state.settings, body.input, body.auto_approve, fake, body.language, body.dry_run)
    except launcher.LaunchError as e:
        if e.status == 409:
            return JSONResponse({"detail": e.detail, "run_id": e.run_id}, status_code=409)
        raise HTTPException(e.status, e.detail) from e
    return {"run_id": run_id}
