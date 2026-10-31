"""Artifact listing with opaque ids; only listed files are ever served (spec 2026-10-31-pipeline-console-ui §3.6)."""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Optional

from pipeline.ui.mapping import DIR_TO_NODE
from pipeline.ui.runs import read_jsonl

TEXT_CAP = 2 * 1024 * 1024
TEXT_KINDS = {"markdown", "csv", "json", "text"}
_KIND_BY_SUFFIX = {
    ".md": "markdown", ".csv": "csv", ".json": "json", ".txt": "text", ".log": "text",
    ".png": "image", ".jpg": "image", ".jpeg": "image", ".webp": "image", ".gif": "image",
}
CONTENT_TYPES = {
    "markdown": "text/markdown; charset=utf-8", "csv": "text/csv; charset=utf-8",
    "json": "application/json", "text": "text/plain; charset=utf-8", "other": "application/octet-stream",
}
_IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp",
                ".gif": "image/gif"}


def kind_of(path: Path) -> str:
    return _KIND_BY_SUFFIX.get(path.suffix.lower(), "other")


def content_type(art: dict) -> str:
    if art["kind"] == "image":
        return _IMAGE_TYPES[Path(art["path"]).suffix.lower()]
    return CONTENT_TYPES[art["kind"]]


def _entry(path: Path, node: Optional[str]) -> dict:
    return {"id": hashlib.sha256(str(path).encode()).hexdigest()[:20], "node": node, "name": path.name,
            "kind": kind_of(path), "bytes": path.stat().st_size, "path": path}


def list_artifacts(rd: Path) -> list[dict]:
    """External `end.outputs` first (event order), then every file under the agent subdirs. Entries keep `path`."""
    rd = rd.resolve()
    out, seen = [], set()
    for ev in read_jsonl(rd / "events.jsonl"):
        outputs = ev.get("outputs") if ev.get("event") == "end" else None
        for value in (outputs or {}).values() if isinstance(outputs, dict) else ():
            path = Path(str(value)).resolve()
            if path in seen or path.is_relative_to(rd) or not path.is_file():
                continue
            seen.add(path)
            out.append(_entry(path, ev.get("node")))
    for sub in sorted(p for p in rd.iterdir() if p.is_dir()):
        for path in sorted(p for p in sub.rglob("*") if p.is_file()):
            out.append(_entry(path, DIR_TO_NODE.get(sub.name)))
    return out


def find_artifact(rd: Path, artifact_id: str) -> Optional[dict]:
    return next((a for a in list_artifacts(rd) if a["id"] == artifact_id), None)
