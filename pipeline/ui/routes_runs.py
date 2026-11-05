"""C3 runs, run detail and artifacts routes."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, Response

from pipeline.ui import actions, artifacts, runs

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
    settings = request.app.state.settings
    return {**run, **actions.available(settings, runs.run_dir(settings.processed_root, run_id), run)}


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


@router.put("/api/runs/{run_id}/artifacts/{artifact_id}")
async def put_artifact(request: Request, run_id: str, artifact_id: str) -> dict:
    rd = _run_dir(request, run_id)
    if runs._record(rd, None)["status"] in runs.ACTIVE:
        raise HTTPException(409, "run active")
    art = artifacts.find_artifact(rd, artifact_id)
    if art is None:
        raise HTTPException(404, "unknown artifact")
    if art["kind"] not in artifacts.TEXT_KINDS:
        raise HTTPException(415, "only text artifacts can be edited")
    if artifacts.is_package_file(art):
        raise HTTPException(409, "edit a package through the publish routes")
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > artifacts.TEXT_CAP:
            raise HTTPException(413, "over the text cap")
    try:
        body.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(400, "not UTF-8") from None
    artifacts.write_text(art, bytes(body))
    return {"bytes": len(body)}
