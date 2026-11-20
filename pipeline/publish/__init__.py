"""Publishing core (spec 2026-11-03-console-operability §1.3): the node and the CLI share `run_target`.

Adapters import lazily: the UI server imports this package and must not load a client that reads secrets.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable

from pipeline import dry_run
from pipeline.publish import package as _package


def _wordpress(run_dir: Path, package: dict) -> dict:
    from pipeline.publish.wordpress import publish  # noqa: PLC0415

    return publish(run_dir, package)


def _youtube(run_dir: Path, package: dict) -> dict:
    from pipeline.publish.youtube import publish  # noqa: PLC0415

    return publish(run_dir, package)


def _blog(run_dir: Path, package: dict) -> dict:
    from pipeline.publish.blog import publish  # noqa: PLC0415

    return publish(run_dir, package)


TARGETS: dict[str, Callable[[Path, dict], dict]] = {"wordpress": _wordpress, "youtube": _youtube, "blog": _blog}


def run_target(run_dir: Path, target: str) -> dict[str, Any]:
    """Publish the package once and record `published` or `failed`. Removes the lock in a finally.

    A lock that already exists is the console's, taken before it spawned this process.
    """
    if dry_run.recorded(run_dir):
        raise ValueError(f"{run_dir} is a dry run: nothing is published")
    package = _package.read(run_dir, target)
    if package is None:
        raise FileNotFoundError(f"no {target} package in {run_dir}")
    _package.acquire_lock(run_dir, target, os.getpid())
    try:
        if package["status"] == "published":
            return package
        package = _package.write(run_dir, {**package, "status": "publishing", "error": None})
        try:
            if target not in TARGETS:
                raise ValueError(f"no adapter for target {target!r}")
            result = TARGETS[target](run_dir, package)
        except Exception as exc:  # noqa: BLE001 - any failure must land in the package, never leave it publishing
            return _package.write(run_dir, {**package, "status": "failed", "error": f"{type(exc).__name__}: {exc}"})
        return _package.write(run_dir, {**package, "status": "published", "result": result, "error": None})
    finally:
        _package.release_lock(run_dir, target)


def _blog_unpublish(run_dir: Path, package: dict) -> dict:
    from pipeline.publish.blog import unpublish  # noqa: PLC0415

    return unpublish(run_dir, package)


UNPUBLISHERS: dict[str, Callable[[Path, dict], dict]] = {"blog": _blog_unpublish}


def unpublish_target(run_dir: Path, target: str) -> dict[str, Any]:
    """Take a published package down and record `unpublished`; a failure keeps it `published` with the error.

    `unpublished` is republishable: save and publish treat it as a draft. Removes the lock in a finally.
    """
    if target not in UNPUBLISHERS:
        raise ValueError(f"{target} has no unpublish")
    package = _package.read(run_dir, target)
    if package is None:
        raise FileNotFoundError(f"no {target} package in {run_dir}")
    _package.acquire_lock(run_dir, target, os.getpid())
    try:
        if package["status"] == "unpublished":
            return package
        if package["status"] not in {"published", "unpublishing"}:  # the console sets `unpublishing` before it spawns
            raise ValueError(f"{target} is {package['status']}, not published")
        package = _package.write(run_dir, {**package, "status": "unpublishing", "error": None})
        try:
            done = UNPUBLISHERS[target](run_dir, package)
        except Exception as exc:  # noqa: BLE001 - the post may still be live: never leave it unpublishing
            return _package.write(run_dir, {**package, "status": "published",
                                            "error": f"unpublish failed: {type(exc).__name__}: {exc}"})
        return _package.write(run_dir, {**package, "status": "unpublished", "result": None, "unpublished": done,
                                        "error": None})
    finally:
        _package.release_lock(run_dir, target)
