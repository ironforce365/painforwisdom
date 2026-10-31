"""§5 launch API and fake runner (spec 2026-10-31-pipeline-console-ui)."""
from __future__ import annotations

import ast
import json
import os
import shutil
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from pipeline.ui import fake_runner, launcher, runs
from pipeline.ui.app import create_app
from pipeline.ui.settings import Settings

FIX = Path(__file__).parent / "fixtures" / "ui"
REPO = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _ckpt(tmp_path, monkeypatch):
    monkeypatch.setenv("CHECKPOINT_DB_PATH", str(tmp_path / "ck.db"))
    from pipeline import graph

    monkeypatch.setattr(graph, "CHECKPOINT_DB", tmp_path / "ck.db")


@pytest.fixture
def processed(tmp_path) -> Path:
    shutil.copytree(FIX / "processed", tmp_path / "processed")
    return tmp_path / "processed"


@pytest.fixture
def spawned(monkeypatch) -> list:
    calls: list = []

    def fake_spawn(argv, log_path):
        calls.append((argv, log_path))
        return os.getpid()  # alive, so the provisional run reads as running

    monkeypatch.setattr(launcher, "spawn", fake_spawn)
    return calls


def _client(processed: Path, runner: str = "real", inbox: Path = FIX / "inbox") -> TestClient:
    return TestClient(create_app(Settings(processed_root=processed, inbox=inbox, runner=runner)))


# --- listing ---------------------------------------------------------------

def test_list_inputs_filters_and_sorts(tmp_path):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    for i, name in enumerate(["a.txt", "b.MOV", "c.mp4", "notes.md", ".hidden.txt"]):
        p = inbox / name
        p.write_text("x" * (i + 1))
        os.utime(p, (1000 + i, 1000 + i))
    (inbox / "sub.mp4").mkdir()
    got = launcher.list_inputs(inbox)
    assert [g["name"] for g in got] == ["c.mp4", "b.MOV", "a.txt"]
    assert got[0] == {"name": "c.mp4", "kind": "video", "bytes": 3, "mtime": 1002.0}
    assert got[2]["kind"] == "transcript"


def test_list_inputs_missing_inbox(tmp_path):
    assert launcher.list_inputs(tmp_path / "nope") == []


def test_inputs_route(processed):
    body = _client(processed).get("/api/inputs").json()
    assert body["inbox"] == str((FIX / "inbox").resolve())
    assert {i["name"]: i["kind"] for i in body["inputs"]} == {
        "PXL_20261030_070000000.mp4": "video", "transcript_2026-10-30.txt": "transcript"}


def test_video_extensions_match_run_py():
    tree = ast.parse((REPO / "pipeline" / "run.py").read_text())
    value = next(n.value for n in tree.body if isinstance(n, ast.Assign)
                 and getattr(n.targets[0], "id", None) == "VIDEO_EXTENSIONS")
    assert ast.literal_eval(value) == launcher.VIDEO_EXTENSIONS


# --- argv ------------------------------------------------------------------

def _settings(runner="real", profile="sandbox", root=Path("/p")) -> Settings:
    return Settings(processed_root=root, inbox=Path("/in"), runner=runner, profile=profile)


def test_argv_real_video_and_transcript():
    s = _settings()
    assert launcher.build_argv(s, "r1", Path("/in/v.mp4"), "video", False, None) == [
        sys.executable, "-m", "pipeline.run", "--profile", "sandbox", "--run-id", "r1", "--video", "/in/v.mp4"]
    assert launcher.build_argv(_settings(profile="prod"), "r2", Path("/in/t.txt"), "transcript", True, None) == [
        sys.executable, "-m", "pipeline.run", "--profile", "prod", "--run-id", "r2",
        "--from-transcript", "/in/t.txt", "--auto-approve"]


def test_argv_fake_defaults_and_explicit():
    s = _settings(runner="fake")
    base = [sys.executable, "-m", "pipeline.ui.fake_runner", "--run-id", "r1", "--processed-root", "/p",
            "--input", "/in/t.txt"]
    assert launcher.build_argv(s, "r1", Path("/in/t.txt"), "transcript", False, None) == [
        *base, "--scenario", "ok", "--step-ms", "200"]
    assert launcher.build_argv(s, "r1", Path("/in/t.txt"), "transcript", True,
                               {"scenario": "hitl", "step_ms": 5}) == [*base, "--scenario", "hitl", "--step-ms", "5"]


def test_new_run_id_unique(tmp_path):
    rid = launcher.new_run_id(tmp_path)
    assert rid.endswith("_ui") and len(rid) == len("2026-10-31_120000_ui")
    (tmp_path / rid).mkdir()
    rid2 = launcher.new_run_id(tmp_path, stamp=rid[:-3])
    assert rid2 == rid + "_2"


# --- POST /api/runs ----------------------------------------------------------

def test_post_fake_with_real_runner_is_400(processed, spawned):
    r = _client(processed).post("/api/runs", json={"input": "transcript_2026-10-30.txt", "fake": {}})
    assert r.status_code == 400 and not spawned


@pytest.mark.parametrize("name", ["missing.txt", "../inbox/transcript_2026-10-30.txt", "/etc/passwd", ""])
def test_post_unknown_input_is_404(processed, spawned, name):
    assert _client(processed).post("/api/runs", json={"input": name}).status_code == 404
    assert not spawned


def test_post_bad_fake_scenario_is_422(processed, spawned):
    r = _client(processed, runner="fake").post(
        "/api/runs", json={"input": "transcript_2026-10-30.txt", "fake": {"scenario": "boom"}})
    assert r.status_code == 422 and not spawned


def test_post_409_only_for_live_pid(processed, spawned):
    client = _client(processed)
    # Fixture placeholders (pid 0) never block.
    assert client.post("/api/runs", json={"input": "transcript_2026-10-30.txt"}).status_code == 201
    rid = spawned[0][1].parent.parent.name
    r = client.post("/api/runs", json={"input": "transcript_2026-10-30.txt"})
    assert r.status_code == 409 and r.json() == {"detail": "run active", "run_id": rid}
    assert len(spawned) == 1


def test_post_409_for_live_fixture(processed, spawned):
    rj = processed / "fx-hitl" / "langgraph" / "run.json"
    rec = json.loads(rj.read_text())
    rj.write_text(json.dumps({**rec, "pid": os.getpid()}))
    r = _client(processed).post("/api/runs", json={"input": "transcript_2026-10-30.txt"})
    assert r.status_code == 409 and r.json()["run_id"] == "fx-hitl"


def test_post_201_writes_provisional_run(processed, spawned):
    client = _client(processed)
    r = client.post("/api/runs", json={"input": "PXL_20261030_070000000.mp4", "auto_approve": True})
    assert r.status_code == 201
    rid = r.json()["run_id"]
    argv, log_path = spawned[0]
    video = str((FIX / "inbox" / "PXL_20261030_070000000.mp4").resolve())
    assert argv[-3:] == ["--video", video, "--auto-approve"] and argv[argv.index("--run-id") + 1] == rid
    assert log_path == processed / rid / "langgraph" / "stdout.log"
    rec = json.loads((processed / rid / "langgraph" / "run.json").read_text())
    assert rec["status"] == "running" and rec["pid"] == os.getpid() and rec["profile"] == "sandbox"
    assert rec["input"] == {"kind": "video", "path": video}
    listed = client.get("/api/runs").json()["runs"]
    assert listed[0]["run_id"] == rid and listed[0]["status"] == "running"


def test_provisional_never_overwrites_child_record(tmp_path):
    rd = tmp_path / "r" / "langgraph"
    rd.mkdir(parents=True)
    (rd / "run.json").write_text('{"status": "done"}')
    launcher.write_provisional(rd, {"status": "running"})
    assert json.loads((rd / "run.json").read_text()) == {"status": "done"}
    assert [p.name for p in rd.iterdir()] == ["run.json"]


def test_spawn_detaches_and_logs(tmp_path):
    log = tmp_path / "x" / "stdout.log"
    log.parent.mkdir()
    pid = launcher.spawn([sys.executable, "-c", "import os,sys; print(os.getsid(0) == os.getpid(), os.getcwd())"], log)
    deadline = time.time() + 20
    while runs.pid_alive(pid) and time.time() < deadline:
        time.sleep(0.05)
    assert not runs.pid_alive(pid)  # reaped, not a zombie
    assert log.read_text().split() == ["True", str(launcher.CHECKOUT_ROOT)]


# --- fake runner -----------------------------------------------------------

NODES_TRANSCRIPT = {"extract", "kb_curator", "extract_image", "youtube_upload", "pre_validator", "writer",
                    "research", "notion_blog", "notion_research", "wordpress_draft", "bv_wordpress",
                    "bv_research", "bv_youtube", "summarizer"}


def _events(rd: Path) -> list[dict]:
    return runs.read_jsonl(rd / "events.jsonl")


def test_fake_runner_hitl_in_process(tmp_path):
    inp = tmp_path / "v.mp4"
    inp.write_bytes(b"\0")
    rc = fake_runner.main(["--run-id", "h1", "--processed-root", str(tmp_path / "p"), "--input", str(inp),
                           "--scenario", "hitl", "--step-ms", "10"])
    rd = tmp_path / "p" / "h1" / "langgraph"
    assert rc == 0
    rec = json.loads((rd / "run.json").read_text())
    assert rec["status"] == "done" and rec["verdict"] == "PASS" and rec["hitl_wait_s"] >= 0.03
    ev = _events(rd)
    kb = [(e["event"], e["attempt"]) for e in ev if e["node"] == "kb_curator"]
    assert kb == [("start", 1), ("paused", 1), ("start", 2), ("end", 2)]
    assert ev[0]["node"] == "transcribe"
    run = runs.load_run(tmp_path / "p", "h1", sorted(NODES_TRANSCRIPT | {"transcribe"}))
    assert {n["state"] for n in run["nodes"].values()} == {"done"}


def test_fake_runner_fail_in_process(tmp_path):
    inp = tmp_path / "t.txt"
    inp.write_text("hi")
    rc = fake_runner.main(["--run-id", "f1", "--processed-root", str(tmp_path / "p"), "--input", str(inp),
                           "--scenario", "fail", "--step-ms", "5"])
    rd = tmp_path / "p" / "f1" / "langgraph"
    assert rc == 2
    rec = json.loads((rd / "run.json").read_text())
    assert rec["status"] == "failed" and rec["error"].startswith("RuntimeError:") and rec["ended_at"]
    run = runs.load_run(tmp_path / "p", "f1", sorted(NODES_TRANSCRIPT | {"transcribe"}))
    assert run["nodes"]["research"]["state"] == "error"
    assert run["nodes"]["notion_research"]["state"] == "pending"
    assert run["nodes"]["summarizer"]["state"] == "pending"


def test_fake_runner_spawned_to_completion(processed):
    """One real spawn through the HTTP API (fake runner), polled until done."""
    client = _client(processed, runner="fake")
    r = client.post("/api/runs", json={"input": "transcript_2026-10-30.txt",
                                       "fake": {"scenario": "ok", "step_ms": 10}})
    assert r.status_code == 201
    rid = r.json()["run_id"]
    deadline = time.time() + 60
    while time.time() < deadline:
        status = client.get(f"/api/runs/{rid}").json()["status"]
        if status not in runs.ACTIVE:
            break
        time.sleep(0.1)
    rd = processed / rid / "langgraph"
    detail = client.get(f"/api/runs/{rid}").json()
    assert detail["status"] == "done", (rd / "stdout.log").read_text()
    assert detail["verdict"] == "PASS" and detail["input"]["kind"] == "transcript"
    assert detail["nodes"]["transcribe"]["state"] == "skipped"
    assert all(detail["nodes"][n]["state"] == "done" for n in NODES_TRANSCRIPT)
    tel = detail["telemetry"]
    assert set(tel["per_node"]) >= {"extract", "kb_curator", "writer", "research", "summarizer"}
    assert tel["tokens"]["input"] > 0 and tel["cost_usd"] > 0
    rows = {row["stage"]: row for row in runs.read_jsonl(rd / "runs.jsonl")}
    assert rows["notion-blog"]["page_id"] == "fx-blog-1"
    assert rows["notion-research"]["page_ids"] == ["fx-res-1", "fx-res-2"]
    ends = [e for e in _events(rd) if e["event"] == "end"]
    assert all(isinstance(e["duration_s"], float) and e["noop"] is False for e in ends)
    for e in ends:
        for path in e["outputs"].values():
            assert Path(path).is_file() and Path(path).parent.parent == rd
    # Parallel siblings overlap: writer and research both start before either ends.
    ev = _events(rd)
    idx = {(e["node"], e["event"]): i for i, e in enumerate(ev)}
    assert max(idx[("writer", "start")], idx[("research", "start")]) < min(idx[("writer", "end")],
                                                                           idx[("research", "end")])
    kinds = {a["kind"] for a in client.get(f"/api/runs/{rid}/artifacts").json()["artifacts"]}
    assert kinds >= {"markdown", "csv", "json", "text", "image"}
    # The run is finished, so the next launch is not blocked.
    assert launcher.active_run(processed) is None
