"""C3 result route (spec 2026-10-31-pipeline-console-ui §4)."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from pipeline.ui import result, runs

router = APIRouter()


@router.get("/api/runs/{run_id}/result")
def get_result(request: Request, run_id: str) -> dict:
    state = request.app.state
    if not hasattr(state, "result_cache"):
        state.result_cache = {}
    cache: dict = state.result_cache
    if run_id in cache:
        return cache[run_id]
    rd = runs.run_dir(request.app.state.settings.processed_root, run_id)
    if rd is None:
        raise HTTPException(404, "unknown run")
    rec = runs._record(rd, None)
    out = result.resolve(rec["status"], rec["rows"], result.source_for(request.app.state.settings))
    if out["status"] == "ready":
        cache[run_id] = out
    return out
