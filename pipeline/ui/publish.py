"""C3 publish API logic (spec 2026-11-03-console-operability §6.4). Never imports a client that reads secrets."""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Optional

from pipeline import publishing_config, runtime
from pipeline.publish import package, specs, video_meta
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
    return {t: pkg for t in package.NODE_DIRS if (pkg := reconcile(rd, t)) is not None}


def overview(settings: Settings, rd: Path) -> dict:
    targets = list_packages(rd)
    rec = runs._record(rd, None)
    return {"targets": targets, "prepare": {t: _prepare_state(settings, rd, rec, t, t in targets) for t in package.NODE_DIRS}}


# --- prepare: a package for a run older than the feature, from the run's files -------


_TITLE = re.compile(r"^\s*(?:\*\*Title:\*\*|#)\s+(.+?)\s*$", re.MULTILINE)
_DATED = re.compile(r"PXL_\d{8}_|\d{4}-\d{2}-\d{2}")
_EXCERPT_WORDS = 50


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _strs(value: Any) -> list[str]:
    return [v for v in value if isinstance(v, str)] if isinstance(value, list) else []


def _capped(value: Any, name: str, target_specs: dict) -> str:
    max_ = target_specs[name]["max"]
    text = value if isinstance(value, str) else ""
    return text[:max_] if max_ else text


def _blog_source(rd: Path) -> Optional[tuple[str, str]]:
    """(title, body) from the WordPress bundle, else from the writer's output."""
    post = rd / "wordpress-draft" / "post.md"
    blog = rd / "painforwisdom-writer" / "blog_post.md"
    text = blog.read_text() if blog.is_file() else ""
    m = _TITLE.search(text)
    if post.is_file():
        return (m.group(1) if m else ""), post.read_text()
    if not text:
        return None
    return (m.group(1), (text[:m.start()] + text[m.end():]).strip() + "\n") if m else ("", text)


def _input_video(rd: Path, rec: dict) -> Optional[Path]:
    path = runs.input_file(rd, rec)
    return path if path and launcher.input_kind(path.name) == "video" else None


def _mmddyy(path: Optional[str]) -> str:
    """The date in the input's name; date_from_filename would answer today for an undated one."""
    if not path or not _DATED.search(Path(path).name):
        return ""
    y, m, d = runtime.date_from_filename(path).split("-")
    return f"{m}/{d}/{y[2:]}"


def _wordpress_package(rd: Path, rec: dict, config: dict, source: tuple[str, str]) -> dict:
    title, body = source
    meta = _read_json(rd / "wordpress-draft" / "meta.json")
    featured = next(iter(sorted((rd / "wordpress-draft").glob("featured.*"))), None)
    url = meta.get("youtube_url")
    fields = {
        "title": _capped(meta.get("title") or title, "title", specs.WORDPRESS),
        "body": body,
        "excerpt": _capped(meta.get("excerpt") or " ".join(body.split()[:_EXCERPT_WORDS]), "excerpt", specs.WORDPRESS),
        "tags": _strs(meta.get("tags")) or ["painforwisdom", *config["default_tags"]],
        "categories": list(config["categories"]),
        "status": config["status"],
        "youtube_url": url if isinstance(url, str) and not package.validate_fields(
            {"field_specs": {"youtube_url": specs.WORDPRESS["youtube_url"]}}, {"youtube_url": url}) else "",
    }
    readonly = {"date_mmddyy": meta.get("date_mmddyy") or _mmddyy(rec["input"].get("path")),
                "notion_blog_url": meta.get("notion_blog_url", ""), "notion_blog_page_id": meta.get("notion_blog_page_id", "")}
    assets = [{"role": "featured_image", "path": str(featured.resolve())}] if featured else []
    return package.new_package("wordpress", "manual", fields, specs.WORDPRESS, readonly=readonly, assets=assets)


def _blog_summary(rd: Path, body: str) -> str:
    """The bundle's excerpt, else the post's opening words without its rule and its dated, italic header lines."""
    excerpt = _read_json(rd / "wordpress-draft" / "meta.json").get("excerpt")
    if isinstance(excerpt, str) and excerpt:
        return excerpt
    lines = [ln for ln in body.splitlines() if ln.strip() != "---" and not re.fullmatch(r"\s*\*[^*]+\*\s*", ln)]
    return " ".join(" ".join(lines).split()[:_EXCERPT_WORDS])


def _youtube_package(rd: Path, config: dict, video: Path, language: str) -> dict:
    meta = _read_json(rd / "youtube-upload" / "metadata.json")
    title, description = meta.get("title"), meta.get("description")
    readonly: dict[str, Any] = {"duration_s": None}
    if not meta:
        readonly["warning"] = "no metadata.json"
    blog = _blog_source(rd)
    if blog and not (title and description):  # a run older than the metadata node recorded both empty
        title, description = title or blog[0], description or _blog_summary(rd, blog[1])
        readonly["warning"] = "Title or description comes from the blog post: review it before publishing."
    fields = {
        "title": _capped(title, "title", specs.YOUTUBE),
        "description": _capped(description, "description", specs.YOUTUBE),
        "tags": _strs(meta.get("tags")),
        "privacy_status": config["privacy_status"],
        "kind": config["kind"],
        **video_meta.youtube_fields(video, language),
    }
    return package.new_package("youtube", "manual", fields, specs.YOUTUBE, readonly=readonly,
                               assets=[{"role": "video", "path": str(video)}])


def _prepare_state(settings: Settings, rd: Path, rec: dict, target: str, exists: bool) -> dict:
    profile = rec["profile"] or settings.profile
    mode = publishing_config.load(profile)[target]["mode"]
    if exists:
        reason = "a package already exists"
    elif rec["status"] in runs.ACTIVE:
        reason = "the run is still active"
    elif mode == "off":
        reason = f"{target} publishing is off for profile {profile}"
    elif target == "wordpress":
        reason = None if _blog_source(rd) else "this run has no blog post"
    else:
        reason = None if _input_video(rd, rec) else "this run has no video"
    return {"can": reason is None, "reason": reason, "mode": mode}


def prepare(settings: Settings, run_id: str, target: str) -> dict:
    """Build the package from the run's files. Its mode is `manual` whatever the config says: nothing auto-publishes it."""
    rd = runs.run_dir(settings.processed_root, run_id)
    if rd is None or target not in package.NODE_DIRS:
        raise PublishError(404, {"detail": "unknown run or target"})
    rec = runs._record(rd, None)
    state = _prepare_state(settings, rd, rec, target, reconcile(rd, target) is not None)
    if not state["can"]:
        conflict = reconcile(rd, target) is not None or rec["status"] in runs.ACTIVE
        raise PublishError(409 if conflict else 422, {"detail": state["reason"]})
    catalog = publishing_config.load(rec["profile"] or settings.profile)
    config = catalog[target]
    package.package_path(rd, target).parent.mkdir(parents=True, exist_ok=True)
    if not package.acquire_lock(rd, target, os.getpid()):
        raise PublishError(409, {"detail": "publishing"})
    try:
        if package.read(rd, target) is not None:
            raise PublishError(409, {"detail": "a package already exists"})
        if target == "wordpress":
            return package.write(rd, _wordpress_package(rd, rec, config, _blog_source(rd)))
        return package.write(rd, _youtube_package(rd, config, _input_video(rd, rec), rec["language"] or catalog["language"]))
    finally:
        package.release_lock(rd, target)


def save(settings: Settings, run_id: str, target: str, fields: dict) -> dict:
    """Write the edited fields without publishing. A failed package stays failed until it is published again."""
    rd = runs.run_dir(settings.processed_root, run_id)
    if rd is None:
        raise PublishError(404, {"detail": "unknown run"})
    pkg = reconcile(rd, target)
    if pkg is None:
        raise PublishError(404, {"detail": "no such package"})
    if runs._record(rd, None)["status"] in runs.ACTIVE:
        raise PublishError(409, {"detail": "run active"})
    if pkg["status"] == "published":
        raise _already_published(pkg)
    if pkg["status"] == "publishing":
        raise PublishError(409, {"detail": "publishing"})
    errors = package.validate_fields(pkg, fields, required=False)
    if errors:
        raise PublishError(400, {"detail": errors})
    if not package.acquire_lock(rd, target, os.getpid()):
        raise PublishError(409, {"detail": "publishing"})
    try:
        pkg = package.read(rd, target)
        if pkg["status"] == "published":
            raise _already_published(pkg)
        return package.write(rd, {**pkg, "fields": {**pkg["fields"], **fields}})
    finally:
        package.release_lock(rd, target)


def asset_path(settings: Settings, run_id: str, target: str, role: str) -> Optional[Path]:
    """Only a path the package lists, and only while it is a file."""
    rd = runs.run_dir(settings.processed_root, run_id)
    pkg = package.read(rd, target) if rd is not None else None
    if pkg is None:
        return None
    listed = next((a["path"] for a in pkg.get("assets", []) if a.get("role") == role), None)
    path = rd / str(listed) if listed else None
    return path if path and path.is_file() else None


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
