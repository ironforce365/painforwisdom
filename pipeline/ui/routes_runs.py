"""C3 runs, run detail and artifacts routes."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, Response

from pipeline.ui import artifacts, runs

router = APIRouter()


def _run_dir(request: Request, run_id: str):
    rd = runs.run_dir(request.app.state.settings.processed_root, run_id)
    if rd is None:
        raise HTTPException(404, "unknown run")
    return rd


@router.get("/api/runs")
def list_runs(request: Request) -> dict:
    return {"runs": runs.list_runs(request.app.state.settings.processed_root)}


@router.get("/api/runs/{run_id}")
def get_run(request: Request, run_id: str) -> dict:
    node_ids = [n["id"] for n in request.app.state.graph["nodes"]]
    run = runs.load_run(request.app.state.settings.processed_root, run_id, node_ids)
    if run is None:
        raise HTTPException(404, "unknown run")
    return run


@router.get("/api/runs/{run_id}/artifacts")
def list_artifacts(request: Request, run_id: str) -> dict:
    arts = artifacts.list_artifacts(_run_dir(request, run_id))
    return {"artifacts": [{k: a[k] for k in ("id", "node", "name", "kind", "bytes")} for a in arts]}


@router.get("/api/runs/{run_id}/artifacts/{artifact_id}")
def get_artifact(request: Request, run_id: str, artifact_id: str) -> Response:
    art = artifacts.find_artifact(_run_dir(request, run_id), artifact_id)
    if art is None:
        raise HTTPException(404, "unknown artifact")
    if art["kind"] in artifacts.TEXT_KINDS:
        with open(art["path"], "rb") as f:
            return Response(f.read(artifacts.TEXT_CAP), media_type=artifacts.content_type(art))
    return FileResponse(art["path"], media_type=artifacts.content_type(art))
