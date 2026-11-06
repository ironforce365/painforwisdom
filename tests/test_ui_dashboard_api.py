"""Dashboard backend: delete a run, dismissed items, packages and the knowledge base (spec 2026-11-06-console-dashboards)."""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from pipeline import publishing_config
from pipeline.publish import package
from pipeline.ui import knowledge, launcher, preview, runs
from pipeline.ui.app import create_app
from pipeline.ui.settings import Settings

pytestmark = pytest.mark.integration

FIX = Path(__file__).parent / "fixtures" / "ui"


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    from pipeline import graph

    monkeypatch.setenv("CHECKPOINT_DB_PATH", str(tmp_path / "ck.db"))
    monkeypatch.setattr(graph, "CHECKPOINT_DB", tmp_path / "ck.db")
    monkeypatch.setattr(publishing_config, "CONFIG_PATH", FIX / "publishing.json")


@pytest.fixture
def processed(tmp_path) -> Path:
    shutil.copytree(FIX / "processed", tmp_path / "processed")
    return tmp_path / "processed"


@pytest.fixture
def client(processed) -> TestClient:
    settings = Settings(processed_root=processed, inbox=FIX / "inbox", runner="real", vault_root=FIX / "knowledge")
    return TestClient(create_app(settings), base_url="http://127.0.0.1")


def _set(processed: Path, run_id: str, **fields) -> None:
    path = processed / run_id / "langgraph" / "run.json"
    path.write_text(json.dumps({**json.loads(path.read_text()), **fields}))


def _checkpoints(path: Path, *threads: str) -> None:
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS checkpoints (thread_id TEXT, checkpoint_id TEXT)")
        conn.execute("CREATE TABLE IF NOT EXISTS writes (thread_id TEXT, checkpoint_id TEXT)")
        for thread in threads:
            conn.execute("INSERT INTO checkpoints VALUES (?, 'c')", (thread,))
            conn.execute("INSERT INTO writes VALUES (?, 'c')", (thread,))


def _rows(path: Path, table: str) -> list[str]:
    with sqlite3.connect(path) as conn:
        return [r[0] for r in conn.execute(f"SELECT thread_id FROM {table}")]  # noqa: S608 - a table name from this file


# --- delete -----------------------------------------------------------------


def test_delete_removes_the_run_and_its_checkpoint_only(client, processed, tmp_path):  # [DT-D8]
    _checkpoints(tmp_path / "ck.db", "fx-done", "fx-failed")
    resp = client.delete("/api/runs/fx-done")
    assert resp.status_code == 200 and resp.json() == {"deleted": "fx-done"}
    assert not (processed / "fx-done").exists() and (processed / "fx-failed").is_dir()
    assert client.get("/api/runs/fx-done").status_code == 404
    assert _rows(tmp_path / "ck.db", "checkpoints") == ["fx-failed"] and _rows(tmp_path / "ck.db", "writes") == ["fx-failed"]


def test_a_deleted_run_stops_marking_its_input_as_already_run(client, processed):  # [DT-D8]
    name = "transcript_2026-10-30.txt"
    users = [i for i in client.get("/api/inputs").json()["inputs"] if i["name"] == name][0]["previous_runs"]
    assert users
    for run_id in users:
        _set(processed, run_id, status="done", pid=None)
        assert client.delete(f"/api/runs/{run_id}").status_code == 200
    assert [i for i in client.get("/api/inputs").json()["inputs"] if i["name"] == name][0]["previous_runs"] == []


def test_delete_without_a_checkpoint_database_still_deletes(client, processed, tmp_path):  # [DT-D8]
    (tmp_path / "ck.db").unlink()  # the app opened it at startup
    assert client.delete("/api/runs/fx-done").status_code == 200
    assert not (processed / "fx-done").exists() and not (tmp_path / "ck.db").exists()


def test_delete_unknown_or_escaping_run_is_404(client, processed):  # [DT-D9]
    assert client.delete("/api/runs/nope").status_code == 404
    assert client.delete("/api/runs/..%2F..").status_code == 404
    assert processed.is_dir()


def test_delete_refuses_an_active_run(client, processed):  # [DT-D9]
    _set(processed, "fx-running", pid=os.getpid(), status="running")
    resp = client.delete("/api/runs/fx-running")
    assert resp.status_code == 409 and "active" in resp.json()["detail"]
    assert (processed / "fx-running").is_dir()


def test_delete_refuses_a_run_publishing(client, processed):  # [DT-D9]
    rd = processed / "fx-done" / "langgraph"
    pkg = package.read(rd, "wordpress")
    package.write(rd, {**pkg, "status": "publishing"})
    assert package.acquire_lock(rd, "wordpress", os.getpid())
    resp = client.delete("/api/runs/fx-done")
    assert resp.status_code == 409 and "publishing" in resp.json()["detail"]
    assert (processed / "fx-done").is_dir()


def test_delete_refuses_a_run_holding_its_archived_input(client, processed):  # [DT-D9]
    source = processed / "fx-done" / "source"
    source.mkdir()
    (source / "only-copy.mp4").write_bytes(b"x")
    resp = client.delete("/api/runs/fx-done")
    assert resp.status_code == 409 and "source" in resp.json()["detail"]
    assert (source / "only-copy.mp4").is_file()


def test_delete_refuses_while_a_preview_converts(client, processed, monkeypatch):  # [DT-D9]
    rd = processed / "fx-done" / "langgraph"
    monkeypatch.setattr(preview, "_jobs", {rd.parent / "console-preview" / "youtube-video.mp4"})
    resp = client.delete("/api/runs/fx-done")
    assert resp.status_code == 409 and "preview" in resp.json()["detail"]


def test_the_run_detail_says_whether_it_can_be_deleted(client, processed):  # [DT-D9] [DT-D8]
    assert client.get("/api/runs/fx-done").json()["manage"]["delete"] is None
    _set(processed, "fx-running", pid=os.getpid(), status="running")
    assert "active" in client.get("/api/runs/fx-running").json()["manage"]["delete"]


def test_a_finished_run_can_run_again_unless_the_input_is_gone(client, processed):  # [DT-D7]
    _set(processed, "fx-done", input={"kind": "transcript", "path": str(FIX / "inbox" / "transcript_2026-10-30.txt")})
    assert client.get("/api/runs/fx-done").json()["manage"]["rerun"] is None
    _set(processed, "fx-done", input={"kind": "video", "path": "/gone/v.mp4"})
    assert "gone" in client.get("/api/runs/fx-done").json()["manage"]["rerun"]


def test_rerun_says_another_run_is_active(client, processed):  # [DT-D7]
    _set(processed, "fx-running", pid=os.getpid(), status="running")
    assert "fx-running" in client.get("/api/runs/fx-done").json()["manage"]["rerun"]


# --- dismissed --------------------------------------------------------------


def _item(client, run_id: str) -> dict:
    return next(r for r in client.get("/api/runs").json()["runs"] if r["run_id"] == run_id)


def test_a_dismissed_run_keeps_what_it_asked_for(client):  # [DT-N6] [DT-N7]
    before = _item(client, "fx-failed")
    assert before["dismissed"] is False and before["attention"] and before["dismissed_attention"] == []
    assert client.post("/api/runs/fx-failed/dismiss", json={"dismissed": True}).status_code == 200
    after = _item(client, "fx-failed")
    assert after["dismissed"] is True and after["attention"] == [] and after["dismissed_attention"] == before["attention"]
    assert client.post("/api/runs/fx-failed/dismiss", json={"dismissed": False}).status_code == 200
    assert _item(client, "fx-failed")["attention"] == before["attention"]


def test_the_run_detail_lists_packages_of_every_status(client, processed):  # [DT-D7]
    rd = processed / "fx-done" / "langgraph"
    assert {"target": "wordpress", "status": "pending"} in client.get("/api/runs/fx-done").json()["packages"]
    package.write(rd, {**package.read(rd, "wordpress"), "status": "published", "result": {"id": "1", "url": "https://x.invalid"}})
    run = client.get("/api/runs/fx-done").json()
    assert {"target": "wordpress", "status": "published"} in run["packages"]
    assert all(p["target"] != "wordpress" for p in run["waiting_packages"])


def test_a_broken_package_does_not_break_the_list(processed):  # [DT-D7]
    rd = processed / "fx-done" / "langgraph"
    package.package_path(rd, "wordpress").write_text("{not json")
    assert all(p["target"] != "wordpress" for p in runs._packages(rd))


# --- knowledge base ---------------------------------------------------------


def test_entries_come_newest_first_with_their_themes(client):  # [DT-D10]
    body = client.get("/api/knowledge").json()
    assert [e["slug"] for e in body["entries"]] == ["2026-09-02-rehearse-the-quit-urge", "2026-07-27-conditions-before-willpower",
                                                    "2026-05-15-no-themes-yet"]
    newest = body["entries"][0]
    assert newest == {"slug": "2026-09-02-rehearse-the-quit-urge", "title": "Rehearse the Quit Urge", "date": "2026-09-02",
                      "themes": ["deliberate-discomfort", "body-literacy"]}
    assert body["entries"][2]["themes"] == []
    assert body["entry_count"] == 3 and body["theme_count"] == 2
    assert body["themes"][0] == {"name": "body-literacy", "entries": 2}


def test_limit_cuts_the_list_and_not_the_totals(client):  # [DT-D10]
    body = client.get("/api/knowledge?limit=1").json()
    assert len(body["entries"]) == 1 and body["entry_count"] == 3


def test_one_entry_is_its_markdown(client):  # [DT-D10]
    resp = client.get("/api/knowledge/2026-09-02-rehearse-the-quit-urge")
    assert resp.status_code == 200 and resp.json()["markdown"].startswith("# 2026-09-02")


@pytest.mark.parametrize("slug", ["nope", "..%2F..%2Fsecret", ".hidden"])
def test_an_entry_outside_the_entries_folder_is_404(client, slug):  # [DT-D10]
    assert client.get(f"/api/knowledge/{slug}").status_code == 404


def test_no_vault_reads_as_empty(tmp_path):  # [DT-D10]
    assert knowledge.overview(tmp_path / "missing") == {"entries": [], "themes": [], "entry_count": 0, "theme_count": 0}
    assert knowledge.entry(tmp_path / "missing", "x") is None


def test_an_entry_without_the_expected_lines_still_lists(tmp_path):  # [DT-D10]
    entries = tmp_path / "gonzalo-book" / "entries"
    entries.mkdir(parents=True)
    (entries / "2026-01-02-plain.md").write_text("no heading here\n")
    assert knowledge.overview(tmp_path)["entries"] == [{"slug": "2026-01-02-plain", "title": "2026-01-02-plain", "date": "2026-01-02", "themes": []}]


def test_the_vault_comes_from_the_setting_then_the_profile_env_then_the_default(tmp_path, monkeypatch):  # [DT-D10]
    from pipeline import runtime

    assert knowledge.vault_for(Settings(processed_root=tmp_path, inbox=tmp_path, vault_root=tmp_path / "set")) == tmp_path / "set"
    monkeypatch.setattr(runtime, "STATE_ROOT", tmp_path)
    monkeypatch.setattr(runtime, "VAULT_PATH", tmp_path / "default")
    settings = Settings(processed_root=tmp_path, inbox=tmp_path, profile="prod")
    assert knowledge.vault_for(settings) == tmp_path / "default"
    (tmp_path / ".env").write_text("VAULT_PATH=/from/env\n")
    assert knowledge.vault_for(settings) == Path("/from/env")


def test_settings_read_the_vault_from_the_env(tmp_path):  # [DT-D10]
    settings = Settings.from_env_and_args([], {"PIPELINE_UI_VAULT": str(tmp_path)})
    assert settings.vault_root == tmp_path
    assert Settings.from_env_and_args([], {}).vault_root is None


def test_graph_labels_are_plain_step_names(client):  # [DT-D7]
    labels = {n["id"]: n["label"] for n in client.get("/api/graph").json()["nodes"]}
    assert labels["writer"] == "Write blog" and labels["kb_curator"] == "Knowledge base" and labels["bv_youtube"] == "Check YouTube"


def test_delete_releases_the_launch_lock_when_it_refuses(client, processed):  # [DT-D9]
    _set(processed, "fx-running", pid=os.getpid(), status="running")
    assert client.delete("/api/runs/fx-running").status_code == 409
    assert not launcher._lock.locked()
