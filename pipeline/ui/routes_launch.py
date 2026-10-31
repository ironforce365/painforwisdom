"""C3 inputs and launch routes (spec 2026-10-31-pipeline-console-ui §5)."""
from __future__ import annotations

from typing import Literal, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from pipeline.ui import launcher

router = APIRouter()


class FakeSpec(BaseModel):
    scenario: Literal["ok", "hitl", "fail"] = "ok"
    step_ms: int = Field(200, ge=0, le=60_000)


class LaunchBody(BaseModel):
    input: str
    auto_approve: bool = False
    fake: Optional[FakeSpec] = None


@router.get("/api/inputs")
def list_inputs(request: Request) -> dict:
    inbox = request.app.state.settings.inbox
    return {"inbox": str(inbox.resolve()), "inputs": launcher.list_inputs(inbox)}


@router.post("/api/runs", status_code=201)
def post_run(request: Request, body: LaunchBody):
    fake = body.fake.model_dump() if body.fake is not None else None
    try:
        run_id = launcher.launch(request.app.state.settings, body.input, body.auto_approve, fake)
    except launcher.LaunchError as e:
        if e.status == 409:
            return JSONResponse({"detail": e.detail, "run_id": e.run_id}, status_code=409)
        raise HTTPException(e.status, e.detail) from e
    return {"run_id": run_id}
