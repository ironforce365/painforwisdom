"""C3 publish API logic (spec 2026-11-03-console-operability §6.4). Never imports a client that reads secrets."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Optional

from pipeline.publish import package
from pipeline.ui import launcher, runs
from pipeline.ui.settings import Settings

PUBLISHER_DIED = "publisher died before recording a result"


class PublishError(Exception):
    def __init__(self, status: int, body: dict[str, Any]):
        super().__init__(str(body))
        self.status, self.body = status, body


def reconcile(rd: Path, target: str) -> Optional[dict]:
    """Read the package; a publisher killed before its `finally` must not wedge it in `publishing` forever."""
    if target not in package.NODE_DIRS:
        return None
    lock_exists = package.lock_path(rd, target).exists()
    holder = package.lock_pid(rd, target)  # a lock naming no pid counts as live
    pkg = package.read(rd, target)  # after the lock: a publisher writes its result before it releases
    if pkg is None:
        return None
    dead = holder is not None and not runs.pid_alive(holder)
    if dead:
        package.release_lock(rd, target)
    if pkg["status"] == "publishing" and (dead or not lock_exists):
        return package.write(rd, {**pkg, "status": "failed", "error": PUBLISHER_DIED})
    return pkg


def list_packages(rd: Path) -> dict:
    targets = {t: pkg for t in package.NODE_DIRS if (pkg := reconcile(rd, t)) is not None}
    return {"targets": targets} if targets else {}


def build_argv(settings: Settings, rd: Path, target: str, profile: Optional[str]) -> list[str]:
    if settings.runner == "fake":
        return [sys.executable, "-m", "pipeline.ui.fake_runner", "publish", "--run-dir", str(rd), "--target", target,
                "--step-ms", str(launcher.FAKE_DEFAULTS["step_ms"])]
    return [sys.executable, "-m", "pipeline.publish", "--profile", profile or settings.profile, "--run-dir", str(rd),
            "--target", target]


def _already_published(pkg: dict) -> PublishError:
    return PublishError(409, {"detail": "already published", "url": (pkg.get("result") or {}).get("url")})


def confirm(settings: Settings, run_id: str, target: str, fields: dict) -> dict:
    """Validate, write the fields, set `publishing`, spawn the publisher. The publisher releases the lock."""
    run = runs.load_run(settings.processed_root, run_id, [])
    rd = runs.run_dir(settings.processed_root, run_id)
    if run is None or rd is None:
        raise PublishError(404, {"detail": "unknown run"})
    pkg = reconcile(rd, target)
    if pkg is None:
        raise PublishError(404, {"detail": "no such package"})
    if run["status"] in runs.ACTIVE:
        raise PublishError(409, {"detail": "run active"})
    if pkg["status"] == "published":
        raise _already_published(pkg)
    errors = package.validate_fields(pkg, fields)
    if errors:
        raise PublishError(400, {"detail": errors})
    if not package.acquire_lock(rd, target, os.getpid()):
        raise PublishError(409, {"detail": "publishing"})
    try:
        pkg = package.read(rd, target)  # a publisher may have finished between the check and the lock
        if pkg["status"] == "published":
            raise _already_published(pkg)
        pkg = package.write(rd, {**pkg, "fields": {**pkg["fields"], **fields}, "status": "publishing", "error": None})
        log = package.package_path(rd, target).with_name("publish.log")
        pid = launcher.spawn(build_argv(settings, rd.resolve(), target, run["profile"]), log)
    except PublishError:
        package.release_lock(rd, target)
        raise
    except Exception as exc:
        package.write(rd, {**pkg, "status": "failed", "error": f"spawn failed: {exc}"})
        package.release_lock(rd, target)
        raise
    package.set_lock_pid(rd, target, pid)
    return pkg
