"""C3 read routes and C5 settings of pipeline/ui over the C6 fixtures."""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from pipeline.ui import artifacts
from pipeline.ui.app import create_app
from pipeline.ui.settings import Settings

FIX = Path(__file__).parent / "fixtures" / "ui"


@pytest.fixture
def settings(tmp_path, monkeypatch) -> Settings:
    monkeypatch.setenv("CHECKPOINT_DB_PATH", str(tmp_path / "ck.db"))
    from pipeline import graph

    monkeypatch.setattr(graph, "CHECKPOINT_DB", tmp_path / "ck.db")
    shutil.copytree(FIX / "processed", tmp_path / "processed")
    return Settings(processed_root=tmp_path / "processed", inbox=FIX / "inbox")


@pytest.fixture
def client(settings) -> TestClient:
    return TestClient(create_app(settings))


def test_health_index_and_static(client):
    assert client.get("/health").json() == {"ok": True}
    r = client.get("/")
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]
    assert client.get("/static/index.html").status_code == 200


def test_graph(client):
    g = client.get("/api/graph").json()
    ids = [n["id"] for n in g["nodes"]]
    assert len(ids) == 15 and "__start__" not in ids and "__end__" not in ids
    assert all(set(n) == {"id", "label", "icon"} and n["label"] and n["icon"] for n in g["nodes"])
    edges = {(e["from"], e["to"]) for e in g["edges"]}
    assert ("transcribe", "extract") in edges and ("extract", "kb_curator") in edges
    assert ("bv_youtube", "summarizer") in edges
    assert not any("__" in a or "__" in b for a, b in edges)


def test_runs_list(client):
    body = client.get("/api/runs").json()
    assert body["runs"][0]["run_id"] == "fx-running"
    assert {r["run_id"] for r in body["runs"]} >= {"fx-done", "fx-legacy"}


def test_run_detail(client):
    r = client.get("/api/runs/fx-done").json()
    assert set(r) == {"run_id", "status", "verdict", "profile", "input", "started_at", "ended_at",
                      "hitl_wait_s", "error", "nodes", "telemetry"}
    assert r["status"] == "done" and r["profile"] == "sandbox"
    assert len(r["nodes"]) == 15
    assert set(r["telemetry"]) == {"total_s", "work_s", "tokens", "cost_usd", "per_node"}


def test_unknown_run_is_404(client):
    assert client.get("/api/runs/nope").status_code == 404
    assert client.get("/api/runs/nope/artifacts").status_code == 404
    assert client.get("/api/runs/nope/artifacts/abc").status_code == 404


def test_artifacts_list_and_fetch(client):
    arts = client.get("/api/runs/fx-done/artifacts").json()["artifacts"]
    assert all(set(a) == {"id", "node", "name", "kind", "bytes"} for a in arts)
    by_kind = {a["kind"]: a for a in arts}
    md = client.get(f"/api/runs/fx-done/artifacts/{by_kind['markdown']['id']}")
    assert md.status_code == 200 and md.headers["content-type"].startswith("text/markdown")
    png = client.get(f"/api/runs/fx-done/artifacts/{by_kind['image']['id']}")
    assert png.headers["content-type"] == "image/png" and png.content.startswith(b"\x89PNG")
    csv = client.get(f"/api/runs/fx-done/artifacts/{by_kind['csv']['id']}")
    assert csv.headers["content-type"].startswith("text/csv")
    js = client.get(f"/api/runs/fx-done/artifacts/{by_kind['json']['id']}")
    assert js.headers["content-type"].startswith("application/json")
    other = client.get(f"/api/runs/fx-done/artifacts/{by_kind['other']['id']}")
    assert other.headers["content-type"] == "application/octet-stream"


@pytest.mark.parametrize("bad", ["..%2Frun.json", "run.json", "deadbeef", "%2Fetc%2Fpasswd"])
def test_artifact_rejects_anything_not_listed(client, bad):
    assert client.get(f"/api/runs/fx-done/artifacts/{bad}").status_code == 404


def test_artifact_other_run_id_is_404(client):
    arts = client.get("/api/runs/fx-done/artifacts").json()["artifacts"]
    assert client.get(f"/api/runs/fx-legacy/artifacts/{arts[0]['id']}").status_code == 404


def test_text_artifact_capped_at_2mb(client, settings):
    big = settings.processed_root / "fx-done" / "langgraph" / "validator" / "big.txt"
    big.write_bytes(b"a" * (artifacts.TEXT_CAP + 10))
    art = next(a for a in client.get("/api/runs/fx-done/artifacts").json()["artifacts"] if a["name"] == "big.txt")
    r = client.get(f"/api/runs/fx-done/artifacts/{art['id']}")
    assert len(r.content) == artifacts.TEXT_CAP


# ---------- settings (C5) ----------

def test_settings_defaults(monkeypatch):
    for k in ("PIPELINE_UI_PORT", "PIPELINE_UI_PROCESSED_ROOT", "PIPELINE_UI_INBOX", "PIPELINE_UI_PROFILE",
              "PIPELINE_UI_RUNNER", "PIPELINE_UI_NOTION_FIXTURES"):
        monkeypatch.delenv(k, raising=False)
    from pipeline import runtime

    s = Settings.from_env_and_args([])
    assert s == Settings(processed_root=runtime.PROCESSED_ROOT, inbox=runtime.STATE_ROOT / "inbox",
                         runner="real", notion_fixtures=None, profile="sandbox", host="127.0.0.1", port=8820)


def test_settings_env_then_cli_override(tmp_path):
    env = {"PIPELINE_UI_PORT": "9000", "PIPELINE_UI_PROCESSED_ROOT": str(tmp_path / "p"),
           "PIPELINE_UI_INBOX": str(tmp_path / "i"), "PIPELINE_UI_PROFILE": "prod",
           "PIPELINE_UI_RUNNER": "fake", "PIPELINE_UI_NOTION_FIXTURES": str(tmp_path / "n")}
    s = Settings.from_env_and_args([], env)
    assert (s.port, s.profile, s.runner, s.notion_fixtures) == (9000, "prod", "fake", tmp_path / "n")
    s = Settings.from_env_and_args(["--port", "9100", "--profile", "sandbox", "--processed-root", "/x",
                                    "--inbox", "/y"], env)
    assert (s.port, s.profile, s.processed_root, s.inbox) == (9100, "sandbox", Path("/x"), Path("/y"))
    assert s.host == "127.0.0.1"


@pytest.mark.parametrize("env", [{"PIPELINE_UI_PROFILE": "live"}, {"PIPELINE_UI_RUNNER": "mock"}])
def test_settings_reject_bad_values(env):
    with pytest.raises(ValueError):
        Settings.from_env_and_args([], env)


def test_main_runs_uvicorn_on_localhost(monkeypatch, settings):
    import uvicorn

    from pipeline.ui import __main__ as entry

    seen = {}
    monkeypatch.setattr(uvicorn, "run", lambda app, host, port: seen.update(host=host, port=port))
    entry.main(["--port", "8899", "--processed-root", str(settings.processed_root)])
    assert seen == {"host": "127.0.0.1", "port": 8899}
