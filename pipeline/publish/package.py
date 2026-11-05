"""C9 publish package: read, atomic write, lock, field validation (spec 2026-11-03-console-operability §1.2).

Standard library only: the UI server imports this module and must not reach a client that reads secrets.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

from pipeline.publish.specs import field_spec  # noqa: F401 - historical home

NODE_DIRS = {"wordpress": "wordpress-draft", "youtube": "youtube-upload"}


def package_path(run_dir: Path, target: str) -> Path:
    return run_dir / NODE_DIRS[target] / "package.json"


def lock_path(run_dir: Path, target: str) -> Path:
    return run_dir / NODE_DIRS[target] / "publish.lock"


def read(run_dir: Path, target: str) -> Optional[dict]:
    """None for an unknown target or a run without that package."""
    if target not in NODE_DIRS:
        return None
    try:
        return json.loads(package_path(run_dir, target).read_text())
    except FileNotFoundError:
        return None


def write(run_dir: Path, package: dict) -> dict:
    """Atomic replace, stamps `updated_at`, returns what was written."""
    written = {**package, "updated_at": time.time()}
    path = package_path(run_dir, package["target"])
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".package.json.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(written, indent=2))
    os.replace(tmp, path)
    return written


def new_package(target: str, mode: str, fields: dict, field_specs: dict, readonly: Optional[dict] = None,
                assets: Optional[list[dict]] = None) -> dict:
    return {"schema": 1, "target": target, "mode": mode, "status": "pending", "fields": fields,
            "field_specs": field_specs, "readonly": readonly or {}, "assets": assets or [], "result": None,
            "error": None, "updated_at": time.time()}


def replay_mode(existing: Optional[dict], configured: str) -> str:
    """The mode of a node's replay: a package already there auto-publishes only if it says so (prepare writes `manual`)."""
    return existing["mode"] if existing and configured == "auto" else configured


def kept_on_replay(existing: Optional[dict], fresh: dict) -> dict:
    """What a node writes when it replays: the user's edits, the mode and the outcome survive; the rest is rebuilt."""
    if existing is None:
        return fresh
    return {**fresh, "fields": {**fresh["fields"], **existing["fields"]}, "mode": existing["mode"],
            "status": existing["status"], "error": existing["error"]}


def acquire_lock(run_dir: Path, target: str, pid: int) -> bool:
    """O_EXCL create holding `pid`; False when a publish already holds the lock."""
    try:
        fd = os.open(lock_path(run_dir, target), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return False
    with os.fdopen(fd, "w") as f:
        f.write(str(pid))
    return True


def set_lock_pid(run_dir: Path, target: str, pid: int) -> None:
    """Hand the lock to the spawned publisher; no-op when it already finished and removed the lock."""
    try:
        fd = os.open(lock_path(run_dir, target), os.O_WRONLY | os.O_TRUNC)
    except FileNotFoundError:
        return
    with os.fdopen(fd, "w") as f:
        f.write(str(pid))


def lock_pid(run_dir: Path, target: str) -> Optional[int]:
    """The holder's pid; None when there is no lock or it names no pid."""
    try:
        return int(lock_path(run_dir, target).read_text())
    except (FileNotFoundError, ValueError):
        return None


def release_lock(run_dir: Path, target: str) -> None:
    lock_path(run_dir, target).unlink(missing_ok=True)


def _valid_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc) and not any(c in value for c in ' \t\r\n"<>')


def _field_errors(name: str, spec: dict, value: Any, required: bool = True) -> list[str]:
    type_, max_ = spec["type"], spec.get("max")
    if type_ == "tags":
        if not isinstance(value, list) or not all(isinstance(t, str) for t in value):
            return [f"{name}: must be a list of strings"]
        empty, size = not value, sum(len(t) for t in value)
    elif not isinstance(value, str):
        return [f"{name}: must be a string"]
    else:
        empty, size = not value.strip(), len(value)
    if required and spec.get("required") and empty:
        return [f"{name}: required"]
    if max_ is not None and size > max_:
        return [f"{name}: over max {max_}"]
    if type_ == "enum" and value not in (spec.get("options") or []):
        return [f"{name}: must be one of {spec.get('options')}"]
    if type_ == "url" and value and not _valid_url(str(value)):
        return [f"{name}: not an http(s) url"]
    return []


def validate_fields(package: dict, fields: dict, required: bool = True) -> list[str]:
    """One `<field>: <problem>` string per bad field; empty means valid. A `tags` max counts total characters.

    `required=False` is a save: types and max only, a draft may be incomplete.
    """
    specs = package["field_specs"]
    errors = [f"{name}: unknown field" for name in fields if name not in specs]
    for name, spec in specs.items():
        if name in fields:
            errors += _field_errors(name, spec, fields[name], required)
        elif required and spec.get("required"):
            errors.append(f"{name}: required")
    return errors
