"""Blog adapter: <slug>.md and its image into the site repo, git push, Notion write-back (OPERATIONS.md §9).

Standard library at import: the console builds blog packages through `new_package` and must not load a client.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

from pipeline.publish import package as _package
from pipeline.publish import specs

SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_DATE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
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


def publish(run_dir: Path, package: dict) -> dict:
    from pipeline.publishing_config import current_profile, load  # noqa: PLC0415

    config = load(current_profile())["blog"]
    fields = package["fields"]
    if not config.get("repo_url") or not config.get("url_template"):
        raise ValueError("blog.repo_url or blog.url_template is empty in config/publishing.json: the site is not set up")
    slug, date = fields["slug"], _DATE.match(fields.get("date", ""))
    if not SLUG.match(slug):
        raise ValueError(f"slug: {slug!r} is not lowercase words joined by '-'")
    if not date:
        raise ValueError(f"date: {fields.get('date')!r} is not YYYY-MM-DD")
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
    _write_back(run_dir, package, url)
    return {"id": sha, "url": url}


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
