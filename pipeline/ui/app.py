"""App factory (spec 2026-10-31-pipeline-console-ui §3). Never imports pipeline.run, never loads .env."""
from __future__ import annotations

import subprocess
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from pipeline.ui import routes_graph, routes_launch, routes_publish, routes_result, routes_runs
from pipeline.ui.settings import Settings

STATIC = Path(__file__).parent / "static"


def read_version(cwd: Path = Path(__file__).parent) -> str:
    """Nearest tag of either release line: coach and pipeline ships both move the checkout."""
    try:
        return subprocess.run(["git", "describe", "--tags", "--always"], cwd=cwd, capture_output=True,
                              text=True, check=True, timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def create_app(settings: Settings) -> FastAPI:
    app = FastAPI(title="pipeline console")
    app.state.settings = settings
    app.state.graph = routes_graph.graph_payload()
    version = read_version()
    for mod in (routes_graph, routes_runs, routes_launch, routes_result, routes_publish):
        app.include_router(mod.router)
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/health")
    def health() -> dict:
        return {"ok": True}

    @app.get("/api/version")
    def get_version() -> dict:
        return {"version": version}

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC / "index.html")

    return app
