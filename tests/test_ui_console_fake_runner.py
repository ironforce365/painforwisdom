"""Fake runner scenarios on C11 and the full loop through the routes (spec 2026-11-05-console-end-to-end section 3)."""
from __future__ import annotations

import json
import os
import shutil
import sys
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from pipeline import hitl
from pipeline.ui import fake_runner, launcher, runs
from pipeline.ui.app import create_app
from pipeline.ui.settings import Settings

FIX = Path(__file__).parent / "fixtures" / "ui"


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    from pipeline import graph

    monkeypatch.setenv("CHECKPOINT_DB_PATH", str(tmp_path / "ck.db"))
    monkeypatch.setattr(graph, "CHECKPOINT_DB", tmp_path / "ck.db")


def _start(tmp_path: Path, scenario: str, *extra: str) -> tuple[dict, Path]:
    inp = tmp_path / "t.txt"
    inp.write_text("x")
    root = tmp_path / "p"
    out: dict = {}
    argv = ["--run-id", "r", "--processed-root", str(root), "--input", str(inp), "--scenario", scenario, "--step-ms", "5", *extra]
    t = threading.Thread(target=lambda: out.setdefault("rc", fake_runner.main(argv)))
    t.start()
    out["thread"] = t
    return out, root / "r" / "langgraph"


def _answer(rd: Path, reply: str, seen: str = "", timeout: float = 20.0) -> dict:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        pending = hitl.read_pending(rd)
        if pending and pending["id"] != seen:
            assert hitl.write_reply(rd, pending["id"], reply)
            return pending
        time.sleep(0.01)
    raise AssertionError("no pending appeared")


def _rec(rd: Path) -> dict:
    return json.loads((rd / "run.json").read_text())


def _kb(rd: Path, node: str) -> list:
    return [(e["event"], e["attempt"]) for e in runs.read_jsonl(rd / "events.jsonl") if e["node"] == node]


def test_hitl_waits_on_the_mailbox(tmp_path):  # [DT-A1]
    out, rd = _start(tmp_path, "hitl")
    pending = _answer(rd, "yes")
    assert pending["kind"] == "approval" and pending["node"] == "kb_curator" and pending["free_text"] is True
    out["thread"].join(30)
    assert out["rc"] == 0
    rec = _rec(rd)
    assert rec["status"] == "done" and rec["hitl_wait_s"] > 0
    assert _kb(rd, "kb_curator") == [("start", 1), ("paused", 1), ("start", 2), ("end", 2)]
    assert not (rd / "pending.json").exists()


def test_hitl_free_text_replans_and_asks_again(tmp_path):  # [DT-A3] [DT-A2]
    out, rd = _start(tmp_path, "hitl")
    first = _answer(rd, "rename it to grit")
    second = _answer(rd, "no", seen=first["id"])
    assert "rename it to grit" in second["prompt"]
    out["thread"].join(30)
    assert out["rc"] == 0 and _rec(rd)["status"] == "done"


def test_hitl_unanswered_times_out_failed(tmp_path):  # [DT-E5]
    out, rd = _start(tmp_path, "hitl", "--wait-s", "1")
    out["thread"].join(30)
    assert out["rc"] == 2
    rec = _rec(rd)
    assert rec["status"] == "failed" and "no answer" in rec["error"]


def test_curator_scenario(tmp_path):  # [DT-A5]
    out, rd = _start(tmp_path, "curator")
    pending = _answer(rd, "keep the old entry")
    assert pending["kind"] == "approval" and "ALREADY_PROCESSED" in pending["prompt"] and pending["node"] == "kb_curator"
    assert "Curator" in pending["title"]
    _answer(rd, "yes", seen=pending["id"])
    out["thread"].join(30)
    assert out["rc"] == 0


def test_error_retry_succeeds(tmp_path):  # [DT-E1]
    out, rd = _start(tmp_path, "error")
    pending = _answer(rd, "retry")
    assert pending["kind"] == "error" and pending["node"] == "research" and pending["free_text"] is False
    assert [o["reply"] for o in pending["options"]] == ["retry", "abort"] and "fake failure" in pending["error"]
    out["thread"].join(30)
    assert out["rc"] == 0 and _rec(rd)["status"] == "done" and _rec(rd)["verdict"] == "PASS"
    assert [e for e, _ in _kb(rd, "research")] == ["start", "error", "start", "end"]


def test_error_abort_fails(tmp_path):  # [DT-E3]
    out, rd = _start(tmp_path, "error")
    _answer(rd, "abort")
    out["thread"].join(30)
    assert out["rc"] == 2
    rec = _rec(rd)
    assert rec["status"] == "failed" and "fake failure" in rec["error"] and rec["ended_at"]


def test_error_twice_asks_again(tmp_path):  # [DT-E2]
    out, rd = _start(tmp_path, "error_twice")
    first = _answer(rd, "retry")
    second = _answer(rd, "retry", seen=first["id"])
    assert second["kind"] == "error"
    out["thread"].join(30)
    assert out["rc"] == 0 and _rec(rd)["status"] == "done"


def test_non_recoverable_only_aborts(tmp_path):  # [DT-E4]
    out, rd = _start(tmp_path, "non_recoverable")
    pending = _answer(rd, "abort")
    assert [o["reply"] for o in pending["options"]] == ["abort"] and pending["non_recoverable"]
    out["thread"].join(30)
    assert out["rc"] == 2 and _rec(rd)["status"] == "failed"


def test_auto_approve_never_waits(tmp_path):  # [DT-S2]
    out, rd = _start(tmp_path, "hitl", "--auto-approve")
    out["thread"].join(30)
    assert out["rc"] == 0 and _rec(rd)["status"] == "done"
    (tmp_path / "x").mkdir()
    out, rd = _start(tmp_path / "x", "error", "--auto-approve")
    out["thread"].join(30)
    assert out["rc"] == 2 and _rec(rd)["status"] == "failed"


def test_retry_subcommand_finishes_the_remaining_nodes(tmp_path):  # [DT-F1]
    out, rd = _start(tmp_path, "fail")
    out["thread"].join(30)
    assert out["rc"] == 2
    before = _rec(rd)
    done_before = [e["node"] for e in runs.read_jsonl(rd / "events.jsonl") if e["event"] == "end"]
    rc = fake_runner.main(["retry", "--run-id", "r", "--processed-root", str(tmp_path / "p"), "--step-ms", "5"])
    assert rc == 0
    rec = _rec(rd)
    assert rec["status"] == "done" and rec["verdict"] == "PASS" and rec["error"] is None and rec["pid"] == os.getpid()
    assert rec["started_at"] == before["started_at"] and rec["ended_at"] > before["ended_at"]
    ends = [e["node"] for e in runs.read_jsonl(rd / "events.jsonl") if e["event"] == "end"]
    assert len(ends) == len(set(ends)), "a finished node ran again"
    assert set(done_before) < set(ends) and "summarizer" in ends
    assert _kb(rd, "research")[-1] == ("end", 2)


def test_retry_subcommand_scenario_can_fail_again(tmp_path):  # [DT-E2] [DT-F1]
    out, rd = _start(tmp_path, "fail")
    out["thread"].join(30)
    rc = fake_runner.main(["retry", "--run-id", "r", "--processed-root", str(tmp_path / "p"), "--step-ms", "5",
                           "--scenario", "fail"])
    assert rc == 2 and _rec(rd)["status"] == "failed"


# --- through the real routes and a real subprocess ---------------------------


@pytest.fixture
def live(tmp_path):
    shutil.copytree(FIX / "processed", tmp_path / "processed")
    settings = Settings(processed_root=tmp_path / "processed", inbox=FIX / "inbox", runner="fake", books_root=FIX / "books")
    client = TestClient(create_app(settings))
    yield client, tmp_path / "processed"
    for child in (tmp_path / "processed").iterdir():
        try:
            pid = json.loads((child / "langgraph" / "run.json").read_text()).get("pid")
        except (OSError, ValueError):
            continue
        if isinstance(pid, int) and pid > 0 and child.name.endswith("_ui"):
            try:
                os.killpg(pid, 9)
            except OSError:
                pass


def _launch(client: TestClient, scenario: str, **fake) -> str:
    r = client.post("/api/runs", json={"input": "transcript_2026-10-30.txt",
                                       "fake": {"scenario": scenario, "step_ms": 5, **fake}})
    assert r.status_code == 201, r.text
    return r.json()["run_id"]


def _poll(client: TestClient, run_id: str, pred, timeout: float = 40.0) -> dict:
    end = time.monotonic() + timeout
    run = {}
    while time.monotonic() < end:
        run = client.get(f"/api/runs/{run_id}").json()
        if pred(run):
            return run
        time.sleep(0.05)
    raise AssertionError(f"timed out, last: {run.get('status')} {run.get('failure')}")


@pytest.mark.integration
def test_console_answers_a_real_subprocess_error_then_retry(live):  # [DT-E1]
    client, _ = live
    rid = _launch(client, "error")
    run = _poll(client, rid, lambda r: r["pending"])
    assert run["status"] == "waiting_reply" and run["actions"] == ["reply", "stop"]
    items = {r["run_id"]: r for r in client.get("/api/runs").json()["runs"]}
    assert items[rid]["attention"][0]["kind"] == "error"
    assert client.post(f"/api/runs/{rid}/reply", json={"id": run["pending"]["id"], "reply": "bad"}).status_code == 400
    assert client.post(f"/api/runs/{rid}/reply", json={"id": "stale", "reply": "retry"}).status_code == 409
    assert client.post(f"/api/runs/{rid}/reply", json={"id": run["pending"]["id"], "reply": "retry"}).status_code == 202
    run = _poll(client, rid, lambda r: r["status"] == "done")
    assert run["verdict"] == "PASS" and run["pending"] is None and run["failure"] is None


@pytest.mark.integration
def test_abort_then_retry_from_checkpoint_then_done(live):  # [DT-E3] [DT-F1]
    client, _ = live
    rid = _launch(client, "error")
    run = _poll(client, rid, lambda r: r["pending"])
    client.post(f"/api/runs/{rid}/reply", json={"id": run["pending"]["id"], "reply": "abort"})
    run = _poll(client, rid, lambda r: r["status"] == "failed")
    assert run["failure"]["node"] == "research" and "retry" in run["actions"]
    r = client.post(f"/api/runs/{rid}/retry", json={})
    assert r.status_code == 202 and r.json() == {"run_id": rid}
    assert client.get(f"/api/runs/{rid}").json()["status"] == "running"
    run = _poll(client, rid, lambda x: x["status"] == "done")
    assert run["verdict"] == "PASS" and run["failure"] is None


@pytest.mark.integration
def test_curator_free_text_then_approve_through_the_routes(live):  # [DT-A5] [DT-A3] [DT-A1]
    client, _ = live
    rid = _launch(client, "curator")
    run = _poll(client, rid, lambda r: r["pending"])
    assert run["status"] == "waiting_hitl" and run["pending"]["free_text"] is True
    first = run["pending"]["id"]
    assert client.post(f"/api/runs/{rid}/reply", json={"id": first, "reply": "keep the old entry"}).status_code == 202
    run = _poll(client, rid, lambda r: r["pending"] and r["pending"]["id"] != first)
    assert "keep the old entry" in run["pending"]["prompt"]
    assert client.post(f"/api/runs/{rid}/reply", json={"id": first, "reply": "yes"}).status_code == 409  # stale
    assert client.post(f"/api/runs/{rid}/reply", json={"id": run["pending"]["id"], "reply": "yes"}).status_code == 202
    assert _poll(client, rid, lambda r: r["status"] == "done")["verdict"] == "PASS"


@pytest.mark.integration
def test_reject_finishes_the_run(live):  # [DT-A2]
    client, _ = live
    rid = _launch(client, "hitl")
    run = _poll(client, rid, lambda r: r["pending"])
    assert client.post(f"/api/runs/{rid}/reply", json={"id": run["pending"]["id"], "reply": "no"}).status_code == 202
    assert _poll(client, rid, lambda r: r["status"] == "done")["pending"] is None


@pytest.mark.integration
def test_answered_elsewhere_the_console_reflects_it(live):  # [DT-A4]
    """hitl.ask took a Telegram answer: it removes pending.json and the runner carries on. Only the mailbox's write differs."""
    client, root = live
    rid = _launch(client, "hitl")
    run = _poll(client, rid, lambda r: r["pending"])
    assert hitl.write_reply(root / rid / "langgraph", run["pending"]["id"], "yes", source="telegram")
    assert _poll(client, rid, lambda r: r["status"] == "done")["pending"] is None


@pytest.mark.integration
def test_error_twice_through_the_routes(live):  # [DT-E2]
    client, _ = live
    rid = _launch(client, "error_twice")
    first = _poll(client, rid, lambda r: r["pending"])["pending"]["id"]
    assert client.post(f"/api/runs/{rid}/reply", json={"id": first, "reply": "retry"}).status_code == 202
    second = _poll(client, rid, lambda r: r["pending"] and r["pending"]["id"] != first)["pending"]["id"]
    assert client.post(f"/api/runs/{rid}/reply", json={"id": second, "reply": "retry"}).status_code == 202
    assert _poll(client, rid, lambda r: r["status"] == "done")["verdict"] == "PASS"


@pytest.mark.integration
def test_non_recoverable_through_the_routes(live):  # [DT-E4]
    client, _ = live
    rid = _launch(client, "non_recoverable")
    run = _poll(client, rid, lambda r: r["pending"])
    assert run["pending"]["non_recoverable"] and [o["reply"] for o in run["pending"]["options"]] == ["abort"]
    assert client.post(f"/api/runs/{rid}/reply", json={"id": run["pending"]["id"], "reply": "retry"}).status_code == 400
    assert client.post(f"/api/runs/{rid}/reply", json={"id": run["pending"]["id"], "reply": "abort"}).status_code == 202
    assert _poll(client, rid, lambda r: r["status"] == "failed")["failure"]["node"] == "research"


@pytest.mark.integration
@pytest.mark.parametrize("fixture", ["fx-stopped", "fx-failed", "fx-crashed"])
def test_retry_a_fixture_run_finishes_it(live, fixture):  # [DT-F1] [DT-F2] [DT-F3]
    client, _ = live
    assert client.post(f"/api/runs/{fixture}/retry", json={}).status_code == 202
    run = _poll(client, fixture, lambda r: r["status"] == "done")
    assert run["verdict"] == "PASS" and run["failure"] is None and run["error"] is None
    assert run["nodes"]["summarizer"]["state"] == "done"


@pytest.mark.integration
def test_unanswered_wait_is_bounded(live):  # [DT-E5]
    client, _ = live
    rid = _launch(client, "non_recoverable", wait_s=1)
    run = _poll(client, rid, lambda r: r["status"] == "failed")
    assert run["failure"] and "no answer" in run["error"]


@pytest.mark.integration
def test_stop_a_real_fake_run_then_restart(live):  # [DT-R3] [DT-F3] [DT-F4]
    client, root = live
    rid = _launch(client, "hitl")
    run = _poll(client, rid, lambda r: r["pending"])
    assert client.post(f"/api/runs/{rid}/stop").status_code == 202
    run = client.get(f"/api/runs/{rid}").json()
    assert run["status"] == "stopped" and run["pending"] is None
    assert not (root / rid / "langgraph" / "pending.json").exists()
    assert set(run["actions"]) == {"retry", "restart", "dismiss"}
    r = client.post(f"/api/runs/{rid}/restart", json={})
    assert r.status_code == 201 and r.json()["run_id"] != rid
    new = r.json()["run_id"]
    assert client.post(f"/api/runs/{new}/stop").status_code == 202


@pytest.mark.integration
def test_launch_accepts_the_new_scenarios_and_wait(tmp_path, monkeypatch):  # [DT-E5]
    calls: list = []
    monkeypatch.setattr(launcher, "spawn", lambda argv, log: calls.append(argv) or os.getpid())
    client = TestClient(create_app(Settings(processed_root=tmp_path, inbox=FIX / "inbox", runner="fake")))
    for scenario in ("error", "error_twice", "non_recoverable", "curator"):
        assert client.post("/api/runs", json={"input": "transcript_2026-10-30.txt",
                                              "fake": {"scenario": scenario, "wait_s": 7}}).status_code == 201
        assert calls[-1][calls[-1].index("--scenario") + 1] == scenario
        assert calls[-1][calls[-1].index("--wait-s") + 1] == "7"
        rd = next(p for p in tmp_path.iterdir() if p.name.endswith("_ui") or "_ui_" in p.name)
        shutil.rmtree(rd)
    assert sys.executable in calls[0]
