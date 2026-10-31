"""Result read path of pipeline/ui (spec 2026-10-31-pipeline-console-ui §4)."""
from __future__ import annotations

import json
import shutil
import sys
import types
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from pipeline.ui import result
from pipeline.ui.app import create_app
from pipeline.ui.settings import Settings

FIX = Path(__file__).parent / "fixtures" / "ui"


@pytest.fixture
def processed(tmp_path, monkeypatch) -> Path:
    from pipeline import graph

    monkeypatch.setattr(graph, "CHECKPOINT_DB", tmp_path / "ck.db")
    shutil.copytree(FIX / "processed", tmp_path / "processed")
    return tmp_path / "processed"


def _client(processed: Path, fixtures: Path | None = FIX / "notion") -> TestClient:
    return TestClient(create_app(Settings(processed_root=processed, inbox=FIX / "inbox", notion_fixtures=fixtures)))


def test_done_run_ready_from_fixtures(processed):
    r = _client(processed).get("/api/runs/fx-done/result")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ready" and body["reason"] is None
    assert set(body["blog"]) == {"title", "url", "markdown"}
    assert body["blog"]["title"] == "the trail that used to beat me"
    assert body["blog"]["markdown"].strip()
    assert [set(x) for x in body["research"]] == [{"title", "type", "url"}] * 2
    assert [x["title"] for x in body["research"]] == [
        json.loads((FIX / "notion" / "research" / f"{i}.json").read_text())["title"] for i in ("fx-res-1", "fx-res-2")]


def test_unknown_run_404(processed):
    assert _client(processed).get("/api/runs/nope/result").status_code == 404


def test_legacy_run_has_no_research(processed, tmp_path):
    fx = tmp_path / "notion"
    shutil.copytree(FIX / "notion", fx)
    shutil.copy(fx / "blog" / "fx-blog-1.json", fx / "blog" / "fx-legacy-blog.json")
    body = _client(processed, fx).get("/api/runs/fx-legacy/result").json()
    assert body["status"] == "ready" and body["research"] is None and body["blog"]["title"]


def test_pending_while_active_and_before_notion_nodes(processed):
    rj = processed / "fx-running" / "langgraph" / "run.json"
    rec = json.loads(rj.read_text())
    rec["pid"] = __import__("os").getpid()
    rj.write_text(json.dumps(rec))
    body = _client(processed).get("/api/runs/fx-running/result").json()
    assert body == {"status": "pending", "reason": None, "blog": None, "research": None}


def test_finished_without_blog_is_unavailable(processed):
    body = _client(processed).get("/api/runs/fx-failed/result").json()
    assert body["status"] == "unavailable" and body["reason"] and body["blog"] is None


def test_missing_fixture_page_is_unavailable(processed, tmp_path):
    (tmp_path / "empty").mkdir()
    r = _client(processed, tmp_path / "empty").get("/api/runs/fx-done/result")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "unavailable" and "Traceback" not in body["reason"] and len(body["reason"]) <= 200


def test_ready_result_is_cached(processed, tmp_path):
    fx = tmp_path / "notion"
    shutil.copytree(FIX / "notion", fx)
    c = _client(processed, fx)
    first = c.get("/api/runs/fx-done/result").json()
    shutil.rmtree(fx)
    assert c.get("/api/runs/fx-done/result").json() == first


def test_unavailable_is_not_cached(processed, tmp_path):
    fx = tmp_path / "notion"
    fx.mkdir()
    c = _client(processed, fx)
    assert c.get("/api/runs/fx-done/result").json()["status"] == "unavailable"
    shutil.copytree(FIX / "notion", fx, dirs_exist_ok=True)
    assert c.get("/api/runs/fx-done/result").json()["status"] == "ready"


def test_source_for_settings(tmp_path):
    s = Settings(processed_root=tmp_path, inbox=tmp_path)
    assert isinstance(result.source_for(s), result.NotionResultSource)
    assert isinstance(result.source_for(Settings(processed_root=tmp_path, inbox=tmp_path, notion_fixtures=tmp_path)),
                      result.FixtureResultSource)


def _prop(kind: str, value):
    if kind in ("title", "rich_text"):
        return {"type": kind, kind: [{"plain_text": value}]}
    if kind == "select":
        return {"type": "select", "select": {"name": value}}
    return {"type": "url", "url": value}


def _fake_notion(pages: dict, fail: Exception | None = None) -> types.SimpleNamespace:
    """Stand-in for pipeline.notion_client: reuses its pure helpers' contract, no network, no env."""
    def retrieve(page_id):
        if fail:
            raise fail
        return pages[page_id]

    def extract_property(page, name):
        p = page["properties"].get(name)
        if p is None:
            return ""
        return {"title": lambda: p["title"][0]["plain_text"], "rich_text": lambda: p["rich_text"][0]["plain_text"],
                "select": lambda: p["select"]["name"], "url": lambda: p["url"]}[p["type"]]()

    client = types.SimpleNamespace(pages=types.SimpleNamespace(retrieve=retrieve))
    return types.SimpleNamespace(
        get_client=lambda: client, get_blog_page=retrieve, get_blog_page_markdown=lambda pid: f"# body of {pid}\n",
        page_url=lambda page: page.get("url", "") or "", extract_property=extract_property)


PAGES = {
    "b1": {"id": "b1", "url": "https://notion.so/b1", "properties": {"Title": _prop("title", "A post")}},
    "r1": {"id": "r1", "url": "https://notion.so/r1", "properties": {
        "Title": _prop("title", "A book"), "Type": _prop("select", "Book"),
        "Source URL": _prop("url", "https://example.org/book")}},
    "r2": {"id": "r2", "url": "https://notion.so/r2", "properties": {
        "Title": _prop("title", "A talk"), "Type": _prop("select", "Video")}},
}


def test_notion_source_maps_pages(monkeypatch):
    monkeypatch.setitem(sys.modules, "pipeline.notion_client", _fake_notion(PAGES))
    src = result.NotionResultSource()
    assert src.blog("b1") == {"title": "A post", "url": "https://notion.so/b1", "markdown": "# body of b1\n"}
    assert src.research(["r1", "r2"]) == [
        {"title": "A book", "type": "Book", "url": "https://example.org/book"},
        {"title": "A talk", "type": "Video", "url": "https://notion.so/r2"},
    ]


def test_notion_error_is_unavailable_not_5xx(processed, monkeypatch):
    monkeypatch.setitem(sys.modules, "pipeline.notion_client",
                        _fake_notion(PAGES, fail=RuntimeError("Could not find page with ID: fx-blog-1 " + "x" * 500)))
    r = _client(processed, fixtures=None).get("/api/runs/fx-done/result")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "unavailable" and body["blog"] is None and body["research"] is None
    assert body["reason"].startswith("RuntimeError") and len(body["reason"]) <= 200


def test_result_module_imports_notion_client_lazily(tmp_path):
    # pipeline.graph pulls notion_client in through the nodes, so check result.py on its own.
    code = (
        "import sys, pathlib\n"
        "from pipeline.ui import result\n"
        "from pipeline.ui.settings import Settings\n"
        f"fx = pathlib.Path({str(FIX)!r})\n"
        "rows = [{'stage': 'notion-blog', 'page_id': 'fx-blog-1'}, {'stage': 'notion-research', 'page_ids': ['fx-res-1']}]\n"
        "src = result.source_for(Settings(processed_root=fx, inbox=fx, notion_fixtures=fx / 'notion'))\n"
        "assert result.resolve('done', rows, src)['status'] == 'ready'\n"
        "result.source_for(Settings(processed_root=fx, inbox=fx))\n"
        "assert 'pipeline.notion_client' not in sys.modules\n"
    )
    import subprocess
    root = Path(__file__).resolve().parent.parent
    proc = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True,
                          env={"PATH": "/usr/bin:/bin", "PYTHONPATH": str(root), "HOME": str(tmp_path)})
    assert proc.returncode == 0, proc.stderr[-2000:]
