"""App factory (spec 2026-10-31-pipeline-console-ui §3). Never imports pipeline.run, never loads .env."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlsplit

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.datastructures import Headers

from pipeline.ui import routes_actions, routes_graph, routes_launch, routes_publish, routes_result, routes_runs
from pipeline.ui.settings import Settings

STATIC = Path(__file__).parent / "static"
LOCAL_HOSTS = {"127.0.0.1", "localhost", "[::1]"}
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
_HOST = re.compile(r"(\[[^\]]*\]|[^:\[\]]*)(?::\d*)?")


def read_version(cwd: Path = Path(__file__).parent) -> str:
    """Nearest tag of either release line: coach and pipeline ships both move the checkout."""
    try:
        return subprocess.run(["git", "describe", "--tags", "--always"], cwd=cwd, capture_output=True,
                              text=True, check=True, timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return "unknown"


class LocalOnly:
    """Serve only requests addressed to this machine, and refuse writes a web page sends from elsewhere.

    The console binds 127.0.0.1, but a browser reaches that for any page: a body-less POST needs no preflight, and DNS
    rebinding makes a hostile name resolve here, so its page can read everything (`Host: evil.example`).
    """

    def __init__(self, app: Any):
        self.app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] == "http" and (refusal := _refusal(scope)):
            await JSONResponse({"detail": refusal}, status_code=403)(scope, receive, send)
            return
        await self.app(scope, receive, send)


def _refusal(scope: Any) -> Optional[str]:
    headers = Headers(scope=scope)
    host = headers.get("host", "")
    match = _HOST.fullmatch(host)
    if not (match and match.group(1).lower() in LOCAL_HOSTS):
        return f"host not allowed: {host!r}"
    origin = headers.get("origin")
    if origin is not None and scope["method"] not in SAFE_METHODS and _netloc(origin) != host.lower():
        return f"origin not allowed: {origin!r}"
    return None


def _netloc(origin: str) -> Optional[str]:
    try:
        return urlsplit(origin).netloc.lower()
    except ValueError:  # http://[bad
        return None


def create_app(settings: Settings) -> FastAPI:
    app = FastAPI(title="pipeline console")
    app.add_middleware(LocalOnly)
    app.state.settings = settings
    app.state.graph = routes_graph.graph_payload()
    version = read_version()
    for mod in (routes_graph, routes_runs, routes_actions, routes_launch, routes_result, routes_publish):
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
