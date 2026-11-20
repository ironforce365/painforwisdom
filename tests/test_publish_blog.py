"""Blog publish target: pushes only to a local bare fixture remote, never a real repo."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from pipeline import publish as publish_core
from pipeline import publishing_config
from pipeline.nodes.wordpress_draft import node_wordpress_draft
from pipeline.publish import blog, package

pytestmark = pytest.mark.integration


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def remote(tmp_path) -> Path:
    bare = tmp_path / "site.git"
    _git(tmp_path, "init", "--bare", "-b", "main", str(bare))
    seed = tmp_path / "seed"
    _git(tmp_path, "clone", str(bare), str(seed))
    (seed / "README.md").write_text("site\n")
    _git(seed, "add", "-A")
    _git(seed, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-m", "seed")
    _git(seed, "push", "origin", "HEAD:main")
    return bare


@pytest.fixture
def config(tmp_path, monkeypatch, remote):
    def setup(**blog_over) -> dict:
        cfg = json.loads(publishing_config.CONFIG_PATH.read_text())
        cfg["blog"].update({"mode": "manual", "repo_url": str(remote),
                            "url_template": "https://blog.test/{yyyy}/{mm}/{dd}/{slug}/", **blog_over})
        cfg["wordpress"]["mode"] = "off"
        path = tmp_path / "publishing.json"
        path.write_text(json.dumps(cfg))
        monkeypatch.setattr(publishing_config, "CONFIG_PATH", path)
        monkeypatch.setenv("PIPELINE_PROFILE", "prod")
        key = tmp_path / "deploy_key"
        key.write_text("k")
        monkeypatch.setenv("BLOG_DEPLOY_KEY", str(key))  # a file-path remote never runs ssh
        return cfg
    return setup


@pytest.fixture(autouse=True)
def probe(monkeypatch) -> list:
    """HTTP statuses the site answers, in order; the last one repeats."""
    statuses = [200]
    monkeypatch.setattr(blog, "_probe", lambda url: statuses.pop(0) if len(statuses) > 1 else statuses[0])
    monkeypatch.setattr(blog, "LIVE_INTERVAL_S", 0)
    return statuses


@pytest.fixture
def notion(monkeypatch) -> list:
    calls: list = []
    monkeypatch.setattr("pipeline.notion_client.update_blog_page_url", lambda *a, **kw: calls.append((a, kw)))
    return calls


def _run(tmp_path: Path, **fields) -> Path:
    rd = tmp_path / "run" / "langgraph"
    image = rd / "wordpress-draft" / "featured.JPG"
    image.parent.mkdir(parents=True)
    image.write_bytes(b"jpg")
    pkg = blog.new_package("manual", title="Heat: the Teacher!", body="Body **text**.\n", excerpt="Ex", tags=["heat"],
                           youtube_url="https://youtu.be/abc", date="2026-10-30", notion_blog_page_id="page-1", image=image)
    package.write(rd, {**pkg, "fields": {**pkg["fields"], **fields}})
    return rd


def test_publish_pushes_post_and_image_and_writes_back(tmp_path, config, remote, notion):
    config()
    rd = _run(tmp_path)
    done = publish_core.run_target(rd, "blog")
    assert done["status"] == "published", done["error"]
    assert done["result"]["url"] == "https://blog.test/2026/10/30/heat-the-teacher/"
    assert done["result"]["id"] == _git(remote, "rev-parse", "main")
    post = _git(remote, "show", "main:src/content/posts/heat-the-teacher.md")
    assert post.startswith('---\ntitle: "Heat: the Teacher!"\ndate: "2026-10-30"\nslug: "heat-the-teacher"\n')
    assert 'image: "heat-the-teacher.jpg"' in post and post.endswith("---\n\nBody **text**.")
    assert _git(remote, "show", "main:public/images/heat-the-teacher.jpg") == "jpg"
    assert notion == [(("page-1", "https://blog.test/2026/10/30/heat-the-teacher/"),
                       {"url_property": "Blog URL", "status": "Published", "excerpt": "Ex"})]


def test_republish_of_same_post_adds_no_commit(tmp_path, config, remote, notion):
    config()
    rd = _run(tmp_path)
    first = blog.publish(rd, package.read(rd, "blog"))
    assert blog.publish(rd, package.read(rd, "blog")) == first
    assert _git(remote, "rev-list", "--count", "main") == "2"


@pytest.mark.parametrize("over,fields,error", [
    ({"repo_url": ""}, {}, "the site is not set up"),
    ({"url_template": ""}, {}, "the site is not set up"),
    ({}, {"slug": "../escape"}, "slug:"),
    ({}, {"date": "30/10/26"}, "date:"),
    ({"content_dir": "../outside"}, {}, "leaves the site repo"),
])
def test_refused_before_anything_is_pushed(tmp_path, config, remote, notion, over, fields, error):
    config(**over)
    rd = _run(tmp_path, **fields)
    done = publish_core.run_target(rd, "blog")
    assert done["status"] == "failed" and error in done["error"]
    assert _git(remote, "rev-list", "--count", "main") == "1" and notion == []


def test_publish_waits_until_page_answers_200(tmp_path, config, notion, probe):
    config()
    probe[:] = [0, 404, 200]
    assert publish_core.run_target(_run(tmp_path), "blog")["status"] == "published"
    assert probe == [200] and len(notion) == 1


def test_page_never_live_fails_after_push_without_notion(tmp_path, config, remote, notion, probe, monkeypatch):
    config()
    probe[:] = [404]
    monkeypatch.setattr(blog, "LIVE_TIMEOUT_S", 0)
    done = publish_core.run_target(_run(tmp_path), "blog")
    sha = _git(remote, "rev-parse", "main")
    assert done["status"] == "failed" and notion == []
    assert done["error"] == (f"RuntimeError: pushed {sha} but https://blog.test/2026/10/30/heat-the-teacher/"
                             " not live after 0s (last status 404)")


@pytest.mark.parametrize("key", ["", "missing"])
def test_deploy_key_preflight_refuses_before_clone(tmp_path, config, remote, notion, monkeypatch, key):
    config()
    monkeypatch.setenv("BLOG_DEPLOY_KEY", str(tmp_path / key) if key else "")
    done = publish_core.run_target(_run(tmp_path), "blog")
    assert done["status"] == "failed" and "BLOG_DEPLOY_KEY" in done["error"] and "OPERATIONS.md §9" in done["error"]
    assert _git(remote, "rev-list", "--count", "main") == "1" and notion == []


def test_failed_write_back_does_not_fail_a_pushed_post(tmp_path, config, monkeypatch):
    config()
    def boom(*a, **kw):
        raise RuntimeError("notion down")

    monkeypatch.setattr("pipeline.notion_client.update_blog_page_url", boom)
    rd = _run(tmp_path)
    assert publish_core.run_target(rd, "blog")["status"] == "published"
    assert "notion down" in (rd / "blog-publish" / "NOTION_UPDATE_FAILED.md").read_text()


def _state(tmp_path: Path) -> dict:
    rd = tmp_path / "node" / "langgraph"
    rd.mkdir(parents=True)
    return {"run_dir": str(rd), "blog_post_title": "My title", "blog_post_text": "**Title:** My title\n\nBody.",
            "video_date": "2026-10-30", "featured_image_path": "", "themes_attached": ["hard-things"],
            "notion_blog_page_id": "page-1", "youtube_url": ""}


@pytest.mark.parametrize("mode", ["off", "manual"])
def test_node_writes_blog_package_by_mode(tmp_path, config, mode):
    config(mode=mode)
    state = _state(tmp_path)
    node_wordpress_draft(state)
    pkg = package.read(Path(state["run_dir"]), "blog")
    if mode == "off":
        assert pkg is None
        return
    assert pkg["status"] == "pending" and pkg["fields"]["slug"] == "my-title" and pkg["fields"]["date"] == "2026-10-30"
    assert pkg["fields"]["tags"] == ["painforwisdom", "hard things"] and "**Title:**" not in pkg["fields"]["body"]
    assert package.validate_fields(pkg, pkg["fields"]) == []


def test_node_auto_mode_pushes_once(tmp_path, config, remote, notion):
    config(mode="auto")
    state = _state(tmp_path)
    node_wordpress_draft(state)
    node_wordpress_draft(state)  # second inbound edge
    assert package.read(Path(state["run_dir"]), "blog")["status"] == "published"
    assert _git(remote, "rev-list", "--count", "main") == "2" and len(notion) == 1
