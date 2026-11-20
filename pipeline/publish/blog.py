"""Blog adapter: <slug>.md and its image into the site repo, git push, Notion write-back; unpublish undoes it (OPERATIONS.md §9).

Standard library at import: the console builds blog packages through `new_package` and must not load a client.
"""
from __future__ import annotations

import html
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit, urlunsplit

from pipeline.publish import package as _package
from pipeline.publish import specs

SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_DATE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
LIVE_TIMEOUT_S, LIVE_INTERVAL_S = 300, 10  # a Pages build takes a minute or two
_AUTHOR = {"GIT_AUTHOR_NAME": "painforwisdom pipeline", "GIT_AUTHOR_EMAIL": "pipeline@painforwisdom.invalid"}


def slugify(title: str) -> str:
    return "-".join(re.findall(r"[a-z0-9]+", title.lower()))[:specs.BLOG["slug"]["max"]].strip("-")


def new_package(mode: str, *, title: str, body: str, excerpt: str, tags: list[str], youtube_url: str, date: str,
                notion_blog_page_id: str = "", image: Optional[Path] = None) -> dict:
    fields = {"title": title[:specs.BLOG["title"]["max"]], "slug": slugify(title), "date": date, "body": body,
              "excerpt": excerpt[:specs.BLOG["excerpt"]["max"]], "tags": tags, "youtube_url": youtube_url}
    return _package.new_package("blog", mode, fields, specs.BLOG,
                                readonly={"notion_blog_page_id": notion_blog_page_id},
                                assets=[{"role": "featured_image", "path": str(image)}] if image else [])


def _front_matter(fields: dict, image_name: str) -> str:
    # JSON strings and lists are valid YAML scalars and flow sequences: no YAML dependency.
    keys = {"title": fields["title"], "date": fields["date"], "slug": fields["slug"], "excerpt": fields.get("excerpt", ""),
            "tags": fields.get("tags", []), "youtube_url": fields.get("youtube_url", ""), "image": image_name}
    return "---\n" + "".join(f"{k}: {json.dumps(v, ensure_ascii=False)}\n" for k, v in keys.items()) + "---\n\n"


def _git(cwd: Optional[Path], *args: str) -> str:
    env = {**os.environ, **_AUTHOR, "GIT_COMMITTER_NAME": _AUTHOR["GIT_AUTHOR_NAME"],
           "GIT_COMMITTER_EMAIL": _AUTHOR["GIT_AUTHOR_EMAIL"], "GIT_TERMINAL_PROMPT": "0"}
    key = os.environ.get("BLOG_DEPLOY_KEY", "").strip()
    if key:  # the site repo's own deploy key, never the user's ssh agent
        env["GIT_SSH_COMMAND"] = f"ssh -i {Path(key).expanduser()} -o IdentitiesOnly=yes -o IdentityAgent=none"
    done = subprocess.run(["git", *args], cwd=cwd, env=env, capture_output=True, text=True, timeout=120)
    if done.returncode:
        raise RuntimeError(f"git {args[0]} failed: {done.stderr.strip()[-500:]}")
    return done.stdout.strip()


def _inside(root: Path, rel: str) -> Path:
    path = (root / rel).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError(f"{rel!r} leaves the site repo")
    return path


def _deploy_key() -> None:
    key = os.environ.get("BLOG_DEPLOY_KEY", "").strip()
    if not key:
        raise ValueError("BLOG_DEPLOY_KEY is unset in the profile env file: see OPERATIONS.md §9")
    if not Path(key).expanduser().is_file():
        raise ValueError(f"BLOG_DEPLOY_KEY {key!r} is not a file: see OPERATIONS.md §9")


def _probe(url: str) -> int:
    """HTTP status of a GET, 0 when the host does not answer."""
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:  # noqa: S310 - url is our own url_template
            return resp.status
    except urllib.error.HTTPError as exc:
        return exc.code
    except (urllib.error.URLError, OSError):
        return 0


def _wait_live(url: str, sha: str) -> None:
    deadline = time.monotonic() + LIVE_TIMEOUT_S
    while (status := _probe(url)) != 200:
        if time.monotonic() >= deadline:
            raise RuntimeError(f"pushed {sha} but {url} not live after {LIVE_TIMEOUT_S}s (last status {status})")
        time.sleep(LIVE_INTERVAL_S)


def _page_title(url: str) -> tuple[int, str]:
    """(HTTP status, unescaped <title>) of a GET; (0, "") when the host does not answer."""
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:  # noqa: S310 - url is our own url_template
            status, page = resp.status, resp.read(500_000).decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, ""
    except (urllib.error.URLError, OSError):
        return 0, ""
    m = re.search(r"<title[^>]*>(.*?)</title>", page, re.IGNORECASE | re.DOTALL)
    return status, html.unescape(m.group(1)).strip() if m else ""


def _wait_gone(url: str, sha: str) -> None:
    """Gone is a 404, or the home page's title: the site answers an unknown path 200 with its home page (kn-f1c82e8a)."""
    parts = urlsplit(url)
    home = urlunsplit((parts.scheme, parts.netloc, "/", "", ""))
    deadline = time.monotonic() + LIVE_TIMEOUT_S
    while True:
        status, title = _page_title(url)
        if status in (404, 410):
            return
        if status == 200 and title and _page_title(home) == (200, title):
            return
        if time.monotonic() >= deadline:
            raise RuntimeError(f"pushed {sha} but {url} still serves the post after {LIVE_TIMEOUT_S}s "
                               f"(last status {status}, title {title!r})")
        time.sleep(LIVE_INTERVAL_S)


def _site_config(fields: dict) -> dict:
    from pipeline.publishing_config import current_profile, load  # noqa: PLC0415

    config = load(current_profile())["blog"]
    if not config.get("repo_url") or not config.get("url_template"):
        raise ValueError("blog.repo_url or blog.url_template is empty in config/publishing.json: the site is not set up")
    if not SLUG.match(fields["slug"]):
        raise ValueError(f"slug: {fields['slug']!r} is not lowercase words joined by '-'")
    return config


def publish(run_dir: Path, package: dict) -> dict:
    fields = package["fields"]
    config = _site_config(fields)
    slug, date = fields["slug"], _DATE.match(fields.get("date", ""))
    if not date:
        raise ValueError(f"date: {fields.get('date')!r} is not YYYY-MM-DD")
    _deploy_key()
    image = next((Path(a["path"]) for a in package["assets"] if a["role"] == "featured_image"), None)
    with tempfile.TemporaryDirectory(prefix="blog-publish-") as tmp:
        repo = Path(tmp) / "site"
        _git(None, "clone", "--depth", "1", "--branch", config["branch"], config["repo_url"], str(repo))
        image_name = f"{slug}{image.suffix.lower()}" if image else ""
        if image:
            target = _inside(repo, config["image_dir"]) / image_name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(image, target)
        post = _inside(repo, config["content_dir"]) / f"{slug}.md"
        post.parent.mkdir(parents=True, exist_ok=True)
        post.write_text(_front_matter(fields, image_name) + fields["body"].strip() + "\n")
        _git(repo, "add", "-A")
        if _git(repo, "status", "--porcelain"):  # a retry after a pushed commit has nothing new
            _git(repo, "commit", "-m", f"post: {fields['title']}")
            _git(repo, "push", "origin", f"HEAD:{config['branch']}")
        sha = _git(repo, "rev-parse", "HEAD")
    yyyy, mm, dd = date.groups()
    url = config["url_template"].format(yyyy=yyyy, mm=mm, dd=dd, slug=slug)
    _wait_live(url, sha)
    _write_back(run_dir, package, url)
    return {"id": sha, "url": url}


def unpublish(run_dir: Path, package: dict) -> dict:
    """git rm the post and its image, push, wait until the page is gone, clear the Notion Blog URL."""
    fields = package["fields"]
    config = _site_config(fields)
    url = (package.get("result") or {}).get("url")
    if not url:
        raise ValueError("the package records no published url: nothing to take down")
    _deploy_key()
    slug = fields["slug"]
    image = next((Path(a["path"]) for a in package["assets"] if a["role"] == "featured_image"), None)
    with tempfile.TemporaryDirectory(prefix="blog-unpublish-") as tmp:
        repo = Path(tmp) / "site"
        _git(None, "clone", "--depth", "1", "--branch", config["branch"], config["repo_url"], str(repo))
        paths = [_inside(repo, config["content_dir"]) / f"{slug}.md"]
        if image:
            paths.append(_inside(repo, config["image_dir"]) / f"{slug}{image.suffix.lower()}")
        present = [str(p.relative_to(repo.resolve())) for p in paths if p.is_file()]
        if present:  # a retry after a pushed removal has nothing left to remove
            _git(repo, "rm", "-q", "--", *present)
            _git(repo, "commit", "-m", f"unpublish: {fields['title']}")
            _git(repo, "push", "origin", f"HEAD:{config['branch']}")
        sha = _git(repo, "rev-parse", "HEAD")
    _wait_gone(url, sha)
    _clear_back(run_dir, package)
    return {"id": sha, "url": url}


def _clear_back(run_dir: Path, package: dict) -> None:
    page = package["readonly"].get("notion_blog_page_id")
    if not page:
        return
    try:
        from pipeline.notion_client import clear_blog_page_url  # noqa: PLC0415

        clear_blog_page_url(page, url_property="Blog URL")
    except Exception as exc:  # noqa: BLE001 - the post is gone; a failed write-back must not read as a failed unpublish
        _package.package_path(run_dir, "blog").with_name("NOTION_UPDATE_FAILED.md").write_text(
            f"Blog post removed from {package['result']['url']} but clearing the Notion Blog URL failed: "
            f"{type(exc).__name__}: {exc}\n")


def _write_back(run_dir: Path, package: dict, url: str) -> None:
    page = package["readonly"].get("notion_blog_page_id")
    if not page:
        return
    try:
        from pipeline.notion_client import update_blog_page_url  # noqa: PLC0415

        update_blog_page_url(page, url, url_property="Blog URL", status="Published",
                             excerpt=package["fields"].get("excerpt", ""))
    except Exception as exc:  # noqa: BLE001 - the post is live; a failed write-back must not read as a failed publish
        _package.package_path(run_dir, "blog").with_name("NOTION_UPDATE_FAILED.md").write_text(
            f"Blog post pushed to {url} but Notion update failed: {type(exc).__name__}: {exc}\n")
