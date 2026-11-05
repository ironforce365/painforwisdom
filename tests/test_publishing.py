"""Publishing core, C3 publish API and WordPress modes (spec 2026-11-03-console-operability §6). No real HTTP."""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from pipeline import publish as publish_core
from pipeline import publishing_config
from pipeline.nodes.validators.branch_wordpress import node_bv_wordpress
from pipeline.nodes.wordpress_draft import FIELD_SPECS, node_wordpress_draft
from pipeline.publish import package
from pipeline.publish import __main__ as cli
from pipeline.ui import fake_runner, launcher
from pipeline.ui.app import create_app
from pipeline.ui.settings import Settings

pytestmark = pytest.mark.integration

FIX = Path(__file__).parent / "fixtures" / "ui"
REPO = Path(__file__).resolve().parent.parent
GOOD = {"title": "T", "body": "B", "excerpt": "", "tags": ["a"], "categories": [], "status": "draft", "youtube_url": ""}


@pytest.fixture(autouse=True)
def _ckpt(tmp_path, monkeypatch):
    from pipeline import graph

    monkeypatch.setattr(graph, "CHECKPOINT_DB", tmp_path / "ck.db")


@pytest.fixture
def processed(tmp_path) -> Path:
    shutil.copytree(FIX / "processed", tmp_path / "processed")
    return tmp_path / "processed"


@pytest.fixture
def spawned(monkeypatch) -> list:
    calls: list = []
    monkeypatch.setattr(launcher, "spawn", lambda argv, log: calls.append((argv, log)) or os.getpid())
    return calls


def client_for(processed: Path, tmp_path: Path, runner: str = "real") -> TestClient:
    return TestClient(create_app(Settings(processed_root=processed, inbox=tmp_path / "i", runner=runner)))


def rd_of(processed: Path, run: str) -> Path:
    return processed / run / "langgraph"


# --- C10 config ------------------------------------------------------------


def test_load_merges_profile_over_base():
    prod, sandbox = publishing_config.load("prod"), publishing_config.load("sandbox")
    assert prod["wordpress"]["mode"] == "manual" and prod["youtube"]["mode"] == "manual"
    assert sandbox["wordpress"]["mode"] == "off" and sandbox["youtube"]["mode"] == "off"
    assert sandbox["wordpress"]["site"] == prod["wordpress"]["site"]
    assert "profiles" not in prod
    assert prod["youtube"]["default_tags"][0] == "painforwisdom"


def test_scraper_writes_only_its_keys_into_the_raw_file(tmp_path, monkeypatch):
    cfg = tmp_path / "publishing.json"
    shutil.copy(publishing_config.CONFIG_PATH, cfg)
    spec = importlib.util.spec_from_file_location("scrape_youtube_tags", REPO / "scripts" / "scrape_youtube_tags.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "CONFIG_PATH", cfg)
    monkeypatch.setattr(mod, "_which_yt_dlp", lambda: "fake")
    monkeypatch.setattr(mod, "_scrape", lambda channel, limit: [{"tags": ["one", "two"]}])
    assert mod.main(["--channel", "@x"]) == 0
    written = json.loads(cfg.read_text())
    assert written["youtube"]["default_tags"] == ["one", "two"] and written["youtube"]["scraped_from_channel"] == "@x"
    assert written["youtube"]["mode"] == "manual" and written["profiles"]["sandbox"]["youtube"]["mode"] == "off"


# --- C9 package ------------------------------------------------------------


@pytest.mark.parametrize("fields,expected", [
    (GOOD, []),
    ({**GOOD, "nope": "x"}, ["nope: unknown field"]),
    ({**GOOD, "title": 3}, ["title: must be a string"]),
    ({**GOOD, "title": "x" * 201}, ["title: over max 200"]),
    ({**GOOD, "title": "  "}, ["title: required"]),
    ({k: v for k, v in GOOD.items() if k != "body"}, ["body: required"]),
    ({**GOOD, "tags": "a"}, ["tags: must be a list of strings"]),
    ({**GOOD, "status": "gone"}, ["status: must be one of ['draft', 'publish']"]),
    ({**GOOD, "youtube_url": "javascript:alert(1)"}, ["youtube_url: not an http(s) url"]),
    ({**GOOD, "youtube_url": 'https://x.y/"><b>'}, ["youtube_url: not an http(s) url"]),
    ({**GOOD, "youtube_url": "https://youtu.be/abc"}, []),
])
def test_validate_fields(fields, expected):
    assert package.validate_fields({"field_specs": FIELD_SPECS}, fields) == expected


def test_tags_max_counts_total_characters():
    pkg = {"field_specs": {"tags": package.field_spec("tags", "Tags", 5)}}
    assert package.validate_fields(pkg, {"tags": ["abc", "de"]}) == []
    assert package.validate_fields(pkg, {"tags": ["abc", "def"]}) == ["tags: over max 5"]


def test_lock_is_exclusive(tmp_path):
    (tmp_path / "wordpress-draft").mkdir()
    assert package.acquire_lock(tmp_path, "wordpress", 7) is True
    assert package.acquire_lock(tmp_path, "wordpress", 8) is False
    assert package.lock_pid(tmp_path, "wordpress") == 7
    package.release_lock(tmp_path, "wordpress")
    package.set_lock_pid(tmp_path, "wordpress", 9)  # the publisher already finished: must not recreate the lock
    assert not package.lock_path(tmp_path, "wordpress").exists()


@pytest.mark.parametrize("run,target", [("fx-done", "wordpress"), ("fx-published", "wordpress"), ("fx-published", "youtube")])
def test_fixture_packages_validate_against_their_own_specs(run, target):
    pkg = package.read(rd_of(FIX / "processed", run), target)
    assert package.validate_fields(pkg, pkg["fields"]) == []


def test_fixture_youtube_package_has_section_7_fields():
    pkg = package.read(rd_of(FIX / "processed", "fx-published"), "youtube")
    assert pkg["status"] == "pending"
    assert set(pkg["fields"]) == {"title", "description", "tags", "privacy_status", "kind"}


# --- C3 publish API --------------------------------------------------------


def test_get_publish(processed, tmp_path):
    client = client_for(processed, tmp_path)
    assert client.get("/api/runs/fx-done/publish").json()["targets"]["wordpress"]["status"] == "pending"
    both = client.get("/api/runs/fx-published/publish").json()["targets"]
    assert both["wordpress"]["status"] == "published" and both["youtube"]["status"] == "pending"
    assert both["wordpress"]["result"]["url"].startswith("https://")
    assert client.get("/api/runs/fx-failed/publish").json()["targets"] == {}
    assert client.get("/api/runs/nope/publish").status_code == 404


def test_post_writes_fields_locks_and_spawns_the_cli(processed, tmp_path, spawned):
    client = client_for(processed, tmp_path)
    r = client.post("/api/runs/fx-done/publish/wordpress", json={"fields": {**GOOD, "title": "Edited"}})
    assert r.status_code == 202
    assert r.json()["status"] == "publishing" and r.json()["fields"]["title"] == "Edited"
    rd = rd_of(processed, "fx-done")
    assert package.read(rd, "wordpress")["fields"]["title"] == "Edited"
    assert package.lock_path(rd, "wordpress").exists()
    argv, log = spawned[0]
    assert argv[1:] == ["-m", "pipeline.publish", "--profile", "sandbox", "--run-dir", str(rd.resolve()),
                        "--target", "wordpress"]
    assert log == rd / "wordpress-draft" / "publish.log"
    second = client.post("/api/runs/fx-done/publish/wordpress", json={"fields": GOOD})
    assert (second.status_code, second.json()) == (409, {"detail": "publishing"})
    assert len(spawned) == 1


def test_post_errors(processed, tmp_path, spawned):
    client = client_for(processed, tmp_path)
    post = lambda run, target, fields: client.post(f"/api/runs/{run}/publish/{target}", json={"fields": fields})  # noqa: E731
    bad = post("fx-done", "wordpress", {**GOOD, "title": "x" * 201, "nope": 1})
    assert bad.status_code == 400 and bad.json()["detail"] == ["nope: unknown field", "title: over max 200"]
    assert post("nope", "wordpress", GOOD).status_code == 404
    assert post("fx-done", "youtube", GOOD).status_code == 404
    assert post("fx-done", "myspace", GOOD).status_code == 404
    done = post("fx-published", "wordpress", GOOD)
    assert done.status_code == 409 and done.json() == {"detail": "already published",
                                                       "url": "https://example.invalid/wordpress/fx-published"}
    assert spawned == []
    assert not package.lock_path(rd_of(processed, "fx-done"), "wordpress").exists()


def test_post_refused_while_run_active(processed, tmp_path, spawned):
    rd = rd_of(processed, "fx-done")
    rec = json.loads((rd / "run.json").read_text())
    (rd / "run.json").write_text(json.dumps({**rec, "status": "running", "pid": os.getpid()}))
    r = client_for(processed, tmp_path).post("/api/runs/fx-done/publish/wordpress", json={"fields": GOOD})
    assert (r.status_code, r.json()) == (409, {"detail": "run active"})
    assert spawned == []


def test_planted_lock_without_pid_counts_as_publishing(processed, tmp_path, spawned):
    lock = package.lock_path(rd_of(processed, "fx-done"), "wordpress")
    lock.touch()
    client = client_for(processed, tmp_path)
    assert client.get("/api/runs/fx-done/publish").json()["targets"]["wordpress"]["status"] == "pending"
    r = client.post("/api/runs/fx-done/publish/wordpress", json={"fields": GOOD})
    assert (r.status_code, r.json()) == (409, {"detail": "publishing"})
    assert lock.exists()


def test_dead_publisher_becomes_failed_and_can_retry(processed, tmp_path, spawned):
    rd = rd_of(processed, "fx-done")
    package.write(rd, {**package.read(rd, "wordpress"), "status": "publishing"})
    package.acquire_lock(rd, "wordpress", 0)  # pid 0 reads as dead
    client = client_for(processed, tmp_path)
    pkg = client.get("/api/runs/fx-done/publish").json()["targets"]["wordpress"]
    assert pkg["status"] == "failed" and "died" in pkg["error"]
    assert not package.lock_path(rd, "wordpress").exists()
    assert client.post("/api/runs/fx-done/publish/wordpress", json={"fields": GOOD}).status_code == 202


def test_spawn_failure_releases_lock_and_records_failed(processed, tmp_path, monkeypatch):
    def boom(argv, log):
        raise OSError("no fork")

    monkeypatch.setattr(launcher, "spawn", boom)
    rd = rd_of(processed, "fx-done")
    client = TestClient(create_app(Settings(processed_root=processed, inbox=tmp_path / "i")),
                        raise_server_exceptions=False)
    assert client.post("/api/runs/fx-done/publish/wordpress", json={"fields": GOOD}).status_code == 500
    assert package.read(rd, "wordpress")["status"] == "failed"
    assert not package.lock_path(rd, "wordpress").exists()


def _settled(client: TestClient, run: str, target: str) -> dict:
    deadline = time.time() + 20
    while time.time() < deadline:
        pkg = client.get(f"/api/runs/{run}/publish").json()["targets"][target]
        if pkg["status"] != "publishing":
            return pkg
        time.sleep(0.05)
    raise AssertionError("publisher did not settle")


@pytest.mark.parametrize("title,status", [("Edited", "published"), ("FAIL", "failed")])
def test_fake_publisher_end_to_end(processed, tmp_path, title, status):
    client = client_for(processed, tmp_path, runner="fake")
    r = client.post("/api/runs/fx-published/publish/youtube",
                    json={"fields": {"title": title, "tags": ["a"], "privacy_status": "private", "kind": "short"}})
    assert r.status_code == 202
    pkg = _settled(client, "fx-published", "youtube")
    assert pkg["status"] == status
    assert not package.lock_path(rd_of(processed, "fx-published"), "youtube").exists()
    if status == "published":
        assert pkg["result"]["url"] == "https://example.invalid/youtube/fx-published"
    else:
        assert pkg["error"]


def test_ui_publish_modules_do_not_load_secret_clients():
    code = ("import sys, pipeline.ui.publish, pipeline.ui.routes_publish;"
            "bad = {'dotenv', 'pipeline.wordpress_client', 'pipeline.notion_client', 'pipeline.youtube_client',"
            " 'pipeline.publish.wordpress'} & set(sys.modules);"
            "sys.exit(str(bad) if bad else 0)")
    out = subprocess.run([sys.executable, "-c", code], cwd=REPO, capture_output=True, text=True)
    assert out.returncode == 0, out.stderr


# --- Fake runner -----------------------------------------------------------


def test_fake_run_writes_manual_wordpress_package(tmp_path):
    transcript = tmp_path / "t.txt"
    transcript.write_text("x")
    assert fake_runner.main(["--run-id", "r1", "--processed-root", str(tmp_path / "p"), "--input", str(transcript),
                             "--step-ms", "1"]) == 0
    rd = tmp_path / "p" / "r1" / "langgraph"
    pkg = package.read(rd, "wordpress")
    assert pkg["mode"] == "manual" and pkg["status"] == "pending"
    assert package.validate_fields(pkg, pkg["fields"]) == []
    row = next(json.loads(ln) for ln in (rd / "runs.jsonl").read_text().splitlines() if "wordpress-draft" in ln)
    assert row["skipped"] is True and row["reason"] == "manual"


# --- CLI -------------------------------------------------------------------


def test_cli_loads_profile_env_then_publishes(processed, tmp_path, monkeypatch):
    (tmp_path / ".env.sandbox").write_text("PFW_TEST_FROM_ENV=https://from.env/post\n")
    monkeypatch.setenv("PAINFORWISDOM_STATE_ROOT", str(tmp_path))
    monkeypatch.setenv("PIPELINE_PROFILE", "unset")
    monkeypatch.setitem(publish_core.TARGETS, "wordpress",
                        lambda rd, pkg: {"id": "1", "url": os.environ["PFW_TEST_FROM_ENV"]})
    rd = rd_of(processed, "fx-done")
    try:
        assert cli.main(["--profile", "sandbox", "--run-dir", str(rd), "--target", "wordpress"]) == 0
        assert os.environ["PIPELINE_PROFILE"] == "sandbox"
    finally:
        os.environ.pop("PFW_TEST_FROM_ENV", None)
    pkg = package.read(rd, "wordpress")
    assert pkg["status"] == "published" and pkg["result"]["url"] == "https://from.env/post"
    assert not package.lock_path(rd, "wordpress").exists()


def test_cli_records_failure_and_removes_console_lock(processed, tmp_path, monkeypatch):
    monkeypatch.setenv("PAINFORWISDOM_STATE_ROOT", str(tmp_path))
    monkeypatch.setenv("PIPELINE_PROFILE", "unset")
    def boom(rd, pkg):
        raise RuntimeError("nope")

    monkeypatch.setitem(publish_core.TARGETS, "wordpress", boom)
    rd = rd_of(processed, "fx-done")
    package.acquire_lock(rd, "wordpress", 123)
    assert cli.main(["--profile", "prod", "--run-dir", str(rd), "--target", "wordpress"]) == 1
    pkg = package.read(rd, "wordpress")
    assert pkg["status"] == "failed" and pkg["error"] == "RuntimeError: nope"
    assert not package.lock_path(rd, "wordpress").exists()


def test_target_without_adapter_fails_cleanly(processed, monkeypatch):
    monkeypatch.delitem(publish_core.TARGETS, "youtube")
    rd = rd_of(processed, "fx-published")
    pkg = publish_core.run_target(rd, "youtube")
    assert pkg["status"] == "failed" and "no adapter" in pkg["error"]


# --- WordPress node modes ---------------------------------------------------


@pytest.fixture
def wp_run(tmp_path, monkeypatch):
    def setup(mode: str) -> dict:
        cfg = json.loads(publishing_config.CONFIG_PATH.read_text())
        cfg["wordpress"].update(mode=mode, categories=["Running"], default_tags=["ultra"])
        path = tmp_path / "publishing.json"
        path.write_text(json.dumps(cfg))
        monkeypatch.setattr(publishing_config, "CONFIG_PATH", path)
        monkeypatch.setenv("PIPELINE_PROFILE", "prod")
        rd = tmp_path / "run" / "langgraph"
        (rd / "wordpress-draft").mkdir(parents=True)
        image = rd / "wordpress-draft" / "featured.jpg"
        image.write_bytes(b"jpg")
        return {"run_dir": str(rd), "blog_post_title": "My title", "blog_post_text": "**Title:** My title\n\nBody **text**.",
                "video_date": "2026-10-30", "featured_image_path": str(image), "themes_attached": ["hard-things"],
                "notion_blog_page_id": "page-1", "notion_blog_url": "https://notion.so/page-1", "youtube_url": ""}
    return setup


def _rows(state: dict) -> list[dict]:
    return [json.loads(ln) for ln in (Path(state["run_dir"]) / "runs.jsonl").read_text().splitlines()]


def _mock_wp(monkeypatch, status: int = 200) -> list[httpx.Request]:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        path = request.url.path
        if path.endswith("/media"):
            return httpx.Response(status, json={"id": 11, "source_url": "https://wp/x.jpg"})
        if path.endswith("/posts"):
            return httpx.Response(status, json={"id": 22, "link": "https://wp/post"})
        if request.method == "GET":
            return httpx.Response(200, json=[])
        return httpx.Response(200, json={"id": 33})

    real = httpx.Client
    monkeypatch.setattr("pipeline.wordpress_client.httpx.Client",
                        lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    monkeypatch.setenv("WORDPRESS_USERNAME", "u")
    monkeypatch.setenv("WORDPRESS_APP_PASSWORD", "p")
    return requests


def test_mode_off_writes_bundle_but_no_package(wp_run, monkeypatch):
    requests = _mock_wp(monkeypatch)
    state = wp_run("off")
    out = node_wordpress_draft(state)
    assert out["wordpress_skipped"] is True and out["wordpress_skip_reason"] == "off"
    assert package.read(Path(state["run_dir"]), "wordpress") is None
    assert (Path(state["run_dir"]) / "wordpress-draft" / "post.html").is_file()
    assert _rows(state)[-1]["skipped"] is True and _rows(state)[-1]["reason"] == "off"
    assert requests == []


def test_mode_manual_writes_pending_package_and_calls_nothing(wp_run, monkeypatch):
    requests = _mock_wp(monkeypatch)
    state = wp_run("manual")
    out = node_wordpress_draft(state)
    assert out["wordpress_skip_reason"] == "manual"
    pkg = package.read(Path(state["run_dir"]), "wordpress")
    assert pkg["status"] == "pending" and pkg["mode"] == "manual"
    assert pkg["fields"]["title"] == "My title" and pkg["fields"]["status"] == "draft"
    assert pkg["fields"]["tags"] == ["painforwisdom", "ultra", "hard things"]
    assert pkg["fields"]["categories"] == ["Running"]
    assert "**Title:**" not in pkg["fields"]["body"]
    assert pkg["assets"] == [{"role": "featured_image", "path": state["featured_image_path"]}]
    assert set(pkg["field_specs"]) == set(pkg["fields"])
    assert package.validate_fields(pkg, pkg["fields"]) == []
    assert _rows(state)[-1]["reason"] == "manual"
    assert requests == []


def test_long_excerpt_is_cut_so_the_package_stays_valid(wp_run):
    state = {**wp_run("manual"), "blog_post_excerpt": "x" * 400}
    node_wordpress_draft(state)
    pkg = package.read(Path(state["run_dir"]), "wordpress")
    assert package.validate_fields(pkg, pkg["fields"]) == []


def test_mode_auto_publishes_once_and_writes_back_to_notion(wp_run, monkeypatch):
    requests = _mock_wp(monkeypatch)
    notion: list = []
    monkeypatch.setattr("pipeline.publish.wordpress.update_blog_page_wordpress_url",
                        lambda page, url, **kw: notion.append((page, url, kw)))
    state = wp_run("auto")
    out = node_wordpress_draft(state)
    assert out["wordpress_url"] == "https://wp/post" and out["wordpress_post_id"] == 22
    pkg = package.read(Path(state["run_dir"]), "wordpress")
    assert pkg["status"] == "published" and pkg["result"] == {"id": "22", "url": "https://wp/post"}
    assert not package.lock_path(Path(state["run_dir"]), "wordpress").exists()
    post = next(r for r in requests if r.url.path.endswith("/posts"))
    body = json.loads(post.content)
    assert body["status"] == "draft" and body["featured_media"] == 11 and body["title"] == "My title"
    assert "<strong>text</strong>" in body["content"] and body["categories"] == [33]
    assert notion[0][:2] == ("page-1", "https://wp/post") and notion[0][2]["status"] == "Draft Created"
    assert _rows(state)[-1]["url"] == "https://wp/post"

    count = len(requests)
    again = node_wordpress_draft(state)  # re-fire from the second inbound edge
    assert again["wordpress_url"] == "https://wp/post" and len(requests) == count
    assert len(notion) == 1


def test_mode_auto_failure_is_recorded_and_does_not_raise(wp_run, monkeypatch):
    _mock_wp(monkeypatch, status=403)
    notion: list = []
    monkeypatch.setattr("pipeline.publish.wordpress.update_blog_page_wordpress_url",
                        lambda *a, **kw: notion.append(a))
    state = wp_run("auto")
    out = node_wordpress_draft(state)
    assert out["wordpress_skipped"] is True and out["wordpress_skip_reason"].startswith("publish error:")
    pkg = package.read(Path(state["run_dir"]), "wordpress")
    assert pkg["status"] == "failed" and "403" in pkg["error"]
    assert "403" in _rows(state)[-1]["error"]
    assert notion == []


def test_notion_failure_does_not_fail_a_created_post(wp_run, monkeypatch):
    _mock_wp(monkeypatch)
    def boom(*a, **kw):
        raise RuntimeError("notion down")

    monkeypatch.setattr("pipeline.publish.wordpress.update_blog_page_wordpress_url", boom)
    state = wp_run("auto")
    assert node_wordpress_draft(state)["wordpress_url"] == "https://wp/post"
    assert "notion down" in (Path(state["run_dir"]) / "wordpress-draft" / "NOTION_UPDATE_FAILED.md").read_text()


def test_edited_fields_reach_the_post(wp_run, monkeypatch):
    requests = _mock_wp(monkeypatch)
    monkeypatch.setattr("pipeline.publish.wordpress.update_blog_page_wordpress_url", lambda *a, **kw: None)
    state = wp_run("manual")
    node_wordpress_draft(state)
    rd = Path(state["run_dir"])
    pkg = package.read(rd, "wordpress")
    package.write(rd, {**pkg, "fields": {**pkg["fields"], "title": "Edited", "body": "New *body*.", "status": "publish",
                                         "youtube_url": "https://youtu.be/abc"}})
    assert publish_core.run_target(rd, "wordpress")["status"] == "published"
    body = json.loads(next(r for r in requests if r.url.path.endswith("/posts")).content)
    assert body["title"] == "Edited" and body["status"] == "publish"
    assert "<em>body</em>" in body["content"] and 'href="https://youtu.be/abc"' in body["content"]


@pytest.mark.parametrize("reason", ["off", "manual"])
def test_validator_off_and_manual_are_not_failures(reason, tmp_path):
    post = tmp_path / "p.md"
    post.write_text("x")
    out = node_bv_wordpress({"blog_post_path": str(post), "featured_image_path": "x", "wordpress_skipped": True,
                             "wordpress_skip_reason": reason, "notion_blog_url": ""})
    finding = next(f for f in out["branch_findings_wordpress"] if f["name"].startswith("WordPress draft"))
    assert finding["ok"] is True and finding["severity"] != "secondary"
