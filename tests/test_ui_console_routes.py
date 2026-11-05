"""Console routes for operating a run end to end (spec 2026-11-05-console-end-to-end section 3). Nothing real is signalled."""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from pipeline import hitl, publishing_config
from pipeline.publish import package
from pipeline.ui import actions, artifacts, launcher, publish
from pipeline.ui.app import create_app
from pipeline.ui.settings import Settings

pytestmark = pytest.mark.integration

FIX = Path(__file__).parent / "fixtures" / "ui"
REPO = Path(__file__).resolve().parent.parent


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
def spawned(monkeypatch) -> list:
    calls: list = []
    monkeypatch.setattr(launcher, "spawn", lambda argv, log: calls.append((argv, log)) or os.getpid())
    return calls


def client_for(processed: Path, runner: str = "fake", **kw) -> TestClient:
    settings = Settings(processed_root=processed, inbox=FIX / "inbox", runner=runner, books_root=FIX / "books", **kw)
    return TestClient(create_app(settings), base_url="http://127.0.0.1")


@pytest.fixture
def client(processed) -> TestClient:
    return client_for(processed)


def rd_of(processed: Path, run: str) -> Path:
    return processed / run / "langgraph"


def rec_of(processed: Path, run: str) -> dict:
    return json.loads((rd_of(processed, run) / "run.json").read_text())


def set_rec(processed: Path, run: str, **fields) -> None:
    (rd_of(processed, run) / "run.json").write_text(json.dumps({**rec_of(processed, run), **fields}))


# --- run detail -------------------------------------------------------------


def test_detail_waiting_with_pending(client, processed):  # [DT-E1] [DT-A1]
    set_rec(processed, "fx-error", pid=os.getpid())
    run = client.get("/api/runs/fx-error").json()
    assert run["status"] == "waiting_reply"
    assert run["pending"]["id"] == "fx-error-pending" and run["pending"]["kind"] == "error"
    assert run["failure"] is None
    assert run["actions"] == ["reply", "stop"]
    assert set(run["action_blockers"]) == {"retry", "restart"}
    assert run["dismissed"] is False


def test_detail_old_release_wait_explains_and_offers_stop(client, processed):  # [DT-E6]
    set_rec(processed, "fx-oldwait", pid=os.getpid())
    run = client.get("/api/runs/fx-oldwait").json()
    assert run["pending"] is None
    assert run["failure"]["node"] == "kb_curator" and "ALREADY_PROCESSED" in run["failure"]["error"]
    assert run["failure"]["hint"]
    assert run["actions"] == ["stop"]


def test_detail_dead_runner_hides_pending_and_reads_crashed(client):  # [DT-F2]
    run = client.get("/api/runs/fx-error").json()
    assert run["status"] == "crashed" and run["pending"] is None
    assert run["failure"]["node"] == "research" and "no rows" in run["failure"]["error"]
    assert run["actions"] == ["retry", "restart", "dismiss"] and run["action_blockers"] == {}


def test_detail_failed_input_gone_blocks_restart(client):  # [DT-F1] [DT-F5]
    run = client.get("/api/runs/fx-failed").json()
    assert run["failure"] == {"node": "research", "error": "RuntimeError: research agent returned no rows",
                              "hint": run["failure"]["hint"]}
    assert run["actions"] == ["retry", "dismiss"]
    assert "gone" in run["action_blockers"]["restart"]


def test_detail_no_checkpoint_blocks_retry(client):  # [DT-F6] [DT-F5]
    run = client.get("/api/runs/fx-nocheckpoint").json()
    assert run["actions"] == ["dismiss"]
    assert "checkpoint" in run["action_blockers"]["retry"] and "gone" in run["action_blockers"]["restart"]


def test_detail_stopped_status_is_finished(client):  # [DT-F3] [DT-F4]
    run = client.get("/api/runs/fx-stopped").json()
    assert run["status"] == "stopped" and run["failure"]["error"]
    assert run["actions"] == ["retry", "restart", "dismiss"]


def test_another_active_run_blocks_retry_and_restart(client, processed):  # [DT-S4] [DT-F1]
    set_rec(processed, "fx-running", pid=os.getpid())
    run = client.get("/api/runs/fx-stopped").json()
    assert run["actions"] == ["dismiss"]
    assert "fx-running" in run["action_blockers"]["retry"] and "fx-running" in run["action_blockers"]["restart"]


def test_inspecting_a_node_gives_its_error_and_its_files(client):  # [DT-R2]
    node = client.get("/api/runs/fx-failed").json()["nodes"]["research"]
    assert node["state"] == "error" and "no rows" in node["error"]
    arts = client.get("/api/runs/fx-done").json()["nodes"]["writer"]
    assert arts["state"] == "done" and arts["error"] is None and arts["outputs"]
    listed = client.get("/api/runs/fx-done/artifacts").json()["artifacts"]
    assert "blog_post.md" in {a["name"] for a in listed if a["node"] == "writer"}


def test_partial_verdict_is_not_a_failure(client, processed):  # [DT-D2]
    set_rec(processed, "fx-unpackaged", verdict="PARTIAL")
    run = client.get("/api/runs/fx-unpackaged").json()
    assert run["verdict"] == "PARTIAL" and run["failure"] is None
    assert client.get("/api/runs/fx-unpackaged/artifacts").status_code == 200


def test_detail_prices_a_subscription_run(client):  # [DT-D6]
    t = client.get("/api/runs/fx-unpackaged").json()["telemetry"]
    kb = t["per_node"]["kb_curator"]
    assert kb["model"] == "claude-opus-5-5" and kb["cost_estimated"] is True
    assert kb["cost_usd"] == pytest.approx((4000 * 4 + 2000 * 20 + 4000 * 5) / 1_000_000)
    assert t["per_node"]["writer"]["cost_usd"] is None and t["per_node"]["writer"]["model"] == "mystery-model"
    assert t["cost_estimated"] is True and t["cost_usd"] > kb["cost_usd"]
    assert client.get("/api/runs/fx-legacy").json()["telemetry"]["cost_usd"] is None  # claude-sonnet-4-6 has no price


def test_done_with_a_pending_package_offers_only_dismiss(client):  # [DT-D1] [DT-F7]
    run = client.get("/api/runs/fx-done").json()
    assert run["failure"] is None and run["pending"] is None
    assert run["actions"] == ["dismiss"] and run["action_blockers"] == {}


def test_real_runner_checks_the_checkpoint_db(processed, tmp_path):  # [DT-F6]
    client = client_for(processed, runner="real")
    assert "checkpoint" in client.get("/api/runs/fx-stopped").json()["action_blockers"]["retry"]
    with sqlite3.connect(tmp_path / "ck.db") as db:
        db.execute("create table checkpoints (thread_id text)")
        db.execute("insert into checkpoints values ('fx-stopped')")
    db.close()
    assert "retry" in client.get("/api/runs/fx-stopped").json()["actions"]
    assert "checkpoint" in client.get("/api/runs/fx-failed").json()["action_blockers"]["retry"]


# --- run list ---------------------------------------------------------------


def test_list_adds_cost_tokens_failed_node_attention(client, processed):  # [DT-N1] [DT-D6]
    set_rec(processed, "fx-error", pid=os.getpid())
    items = {r["run_id"]: r for r in client.get("/api/runs").json()["runs"]}
    assert items["fx-error"]["attention"] == [{"kind": "error", "label": "Error in research"}]
    assert items["fx-failed"]["failed_node"] == "research"
    assert items["fx-failed"]["attention"][0]["kind"] == "failed"
    assert items["fx-crashed"]["attention"][0]["kind"] == "crashed"
    assert items["fx-stopped"]["attention"][0]["kind"] == "stopped"
    assert [a["kind"] for a in items["fx-done"]["attention"]] == ["publish"]
    assert items["fx-unpackaged"]["attention"] == []
    assert items["fx-unpackaged"]["cost_estimated"] is True and items["fx-unpackaged"]["cost_usd"] > 0
    assert items["fx-unpackaged"]["tokens"] == 3000 + 800 + 8000 + 2000 + 500 + 300
    assert items["fx-done"]["cost_estimated"] is False and items["fx-done"]["failed_node"] is None


def test_list_approval_attention(client, processed):  # [DT-N1] [DT-A1]
    set_rec(processed, "fx-hitl", pid=os.getpid())
    items = {r["run_id"]: r for r in client.get("/api/runs").json()["runs"]}
    assert items["fx-hitl"]["attention"][0]["kind"] == "approval"
    (rd_of(processed, "fx-hitl") / "pending.json").write_text(json.dumps(hitl.approval_request("kb_curator", "New theme", "p")))
    items = {r["run_id"]: r for r in client.get("/api/runs").json()["runs"]}
    assert items["fx-hitl"]["attention"] == [{"kind": "approval", "label": "New theme"}]


def test_dismiss_empties_attention_and_is_reversible(client, processed):  # [DT-F7]
    assert client.post("/api/runs/fx-failed/dismiss", json={"dismissed": True}).status_code == 200
    assert rec_of(processed, "fx-failed")["dismissed"] is True
    assert client.get("/api/runs/fx-failed").json()["dismissed"] is True
    items = {r["run_id"]: r for r in client.get("/api/runs").json()["runs"]}
    assert items["fx-failed"]["attention"] == []
    assert client.post("/api/runs/fx-failed/dismiss", json={"dismissed": False}).json() == {"dismissed": False}
    assert client.post("/api/runs/nope/dismiss", json={}).status_code == 404


def test_dismiss_keeps_a_legacy_run_legacy(client, processed):  # [DT-F7]
    before = client.get("/api/runs/fx-legacy").json()
    assert client.post("/api/runs/fx-legacy/dismiss", json={}).json() == {"dismissed": True}
    after = client.get("/api/runs/fx-legacy").json()
    assert after["dismissed"] is True and after["status"] == "done"
    assert after["nodes"] == before["nodes"] and after["verdict"] == before["verdict"]


# --- reply ------------------------------------------------------------------


def _reply(client, run="fx-error", id="fx-error-pending", reply="retry"):
    return client.post(f"/api/runs/{run}/reply", json={"id": id, "reply": reply})


def test_reply_lands_in_the_mailbox(client, processed):  # [DT-E1]
    set_rec(processed, "fx-error", pid=os.getpid())
    r = _reply(client)
    assert r.status_code == 202
    got = json.loads((rd_of(processed, "fx-error") / "reply.json").read_text())
    assert got["id"] == "fx-error-pending" and got["reply"] == "retry" and got["source"] == "console" and got["ts"] > 0


def test_reply_refusals(client, processed):  # [DT-E1] [DT-E4] [DT-A3]
    assert _reply(client, run="nope").status_code == 404
    assert _reply(client).status_code == 409  # dead runner: nothing is pending any more
    set_rec(processed, "fx-error", pid=os.getpid())
    assert _reply(client, id="old").status_code == 409
    assert _reply(client, reply="   ").status_code == 400
    assert _reply(client, reply="do something else").status_code == 400
    assert not (rd_of(processed, "fx-error") / "reply.json").exists()
    set_rec(processed, "fx-oldwait", pid=os.getpid())
    assert _reply(client, run="fx-oldwait").status_code == 409


def test_free_text_is_accepted_where_allowed(client, processed):  # [DT-A3]
    rd = rd_of(processed, "fx-hitl")
    set_rec(processed, "fx-hitl", pid=os.getpid())
    (rd / "pending.json").write_text(json.dumps({**hitl.approval_request("kb_curator", "t", "p"), "id": "h1"}))
    assert _reply(client, run="fx-hitl", id="h1", reply="rename it to grit").status_code == 202
    assert json.loads((rd / "reply.json").read_text())["reply"] == "rename it to grit"


def test_two_concurrent_replies_cannot_both_land(client, processed):  # [DT-A1]
    set_rec(processed, "fx-error", pid=os.getpid())
    results: list = []
    barrier = threading.Barrier(8)

    def one(i):
        barrier.wait()
        results.append(_reply(client, reply=("retry", "abort")[i % 2]).status_code)

    threads = [threading.Thread(target=one, args=(i,)) for i in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sorted(results) == [202] + [409] * 7


def test_a_leftover_reply_with_a_stale_id_does_not_block(client, processed):  # [DT-A1]
    set_rec(processed, "fx-error", pid=os.getpid())
    assert hitl.write_reply(rd_of(processed, "fx-error"), "older", "x")
    assert _reply(client).status_code == 202
    assert json.loads((rd_of(processed, "fx-error") / "reply.json").read_text())["id"] == "fx-error-pending"


# --- stop -------------------------------------------------------------------


def _runner(tmp_path, *extra, argv1: str = "pipeline.run") -> subprocess.Popen:
    """A stand-in runner: its cmdline has the module token, in its own session like a spawned run."""
    code = ("import signal, sys, time; " + "".join(extra) + "print('up', flush=True); time.sleep(60)")
    proc = subprocess.Popen([sys.executable, "-c", code, argv1], stdout=subprocess.PIPE, text=True, start_new_session=True)
    assert proc.stdout.readline().strip() == "up"
    threading.Thread(target=proc.wait, daemon=True).start()
    return proc


def _gone(pid: int, timeout: float = 5.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            if not Path(f"/proc/{pid}/cmdline").read_bytes():
                return True
        except OSError:
            return True
        time.sleep(0.02)
    return False


def test_stop_terminates_and_marks_stopped(client, processed, tmp_path):  # [DT-R3]
    proc = _runner(tmp_path)
    set_rec(processed, "fx-error", pid=proc.pid)
    assert client.post("/api/runs/fx-error/stop").status_code == 202
    assert _gone(proc.pid)
    rec = rec_of(processed, "fx-error")
    assert rec["status"] == "stopped" and rec["ended_at"] > 0
    assert not (rd_of(processed, "fx-error") / "pending.json").exists()
    run = client.get("/api/runs/fx-error").json()
    assert run["status"] == "stopped" and run["failure"] and run["pending"] is None


def test_stop_escalates_to_sigkill(client, processed, tmp_path, monkeypatch):  # [DT-R3]
    monkeypatch.setattr(actions, "STOP_GRACE_S", 0.3)
    proc = _runner(tmp_path, "signal.signal(signal.SIGTERM, signal.SIG_IGN); ")
    set_rec(processed, "fx-running", pid=proc.pid)
    assert client.post("/api/runs/fx-running/stop").status_code == 202
    assert _gone(proc.pid)
    assert rec_of(processed, "fx-running")["status"] == "stopped"


def test_stop_never_signals_a_reused_pid(client, processed, tmp_path):  # [DT-R3]
    proc = _runner(tmp_path, argv1="something-else")
    try:
        set_rec(processed, "fx-error", pid=proc.pid)
        assert client.post("/api/runs/fx-error/stop").status_code == 202
        time.sleep(0.2)
        assert proc.poll() is None
        assert rec_of(processed, "fx-error")["status"] == "stopped"
    finally:
        proc.kill()


def test_stop_refused_when_not_active(client):  # [DT-R3]
    assert client.post("/api/runs/fx-done/stop").status_code == 409
    assert client.post("/api/runs/fx-crashed/stop").status_code == 409
    assert client.post("/api/runs/nope/stop").status_code == 404


def test_stop_leaves_a_run_that_finished_meanwhile(client, processed, tmp_path):  # [DT-R3]
    code = (f"import json, signal, sys, time\n"
            f"p = {str(rd_of(processed, 'fx-running') / 'run.json')!r}\n"
            "def done(*a):\n"
            "    r = json.load(open(p)); r['status'] = 'done'; json.dump(r, open(p, 'w')); sys.exit(0)\n"
            "signal.signal(signal.SIGTERM, done); print('up', flush=True); time.sleep(60)\n")
    proc = subprocess.Popen([sys.executable, "-c", code, "pipeline.run"], stdout=subprocess.PIPE, text=True,
                            start_new_session=True)
    assert proc.stdout.readline().strip() == "up"
    threading.Thread(target=proc.wait, daemon=True).start()
    set_rec(processed, "fx-running", pid=proc.pid)
    assert client.post("/api/runs/fx-running/stop").status_code == 202
    assert _gone(proc.pid)
    assert rec_of(processed, "fx-running")["status"] == "done"


# --- retry and restart ------------------------------------------------------


def _publishing(processed: Path, run: str, lock_pid: int, target: str = "youtube", status: str = "publishing") -> None:
    rd = rd_of(processed, run)
    pkg = package.new_package(target, "manual", {"title": "edited"}, {"title": {"type": "str"}})
    package.write(rd, {**pkg, "status": status})
    package.lock_path(rd, target).write_text(str(lock_pid))


def test_a_publish_in_progress_blocks_retry_and_restart(client, processed, spawned):  # [DT-F3] [DT-P4]
    _publishing(processed, "fx-stopped", os.getpid())
    run = client.get("/api/runs/fx-stopped").json()
    assert run["actions"] == ["dismiss"]
    assert "publishing" in run["action_blockers"]["retry"] and "youtube" in run["action_blockers"]["retry"]
    assert "publishing" in run["action_blockers"]["restart"]
    for verb in ("retry", "restart"):
        resp = client.post(f"/api/runs/fx-stopped/{verb}")
        assert resp.status_code == 409 and "publishing" in resp.json()["detail"]
    assert spawned == [] and rec_of(processed, "fx-stopped")["status"] == "stopped"


def test_a_dead_publisher_no_longer_blocks_retry(client, processed):  # [DT-P11]
    _publishing(processed, "fx-stopped", 2 ** 22 + 1)
    run = client.get("/api/runs/fx-stopped").json()
    assert run["actions"] == ["retry", "restart", "dismiss"] and run["action_blockers"] == {}


def test_a_package_at_rest_does_not_block_retry(client, processed):  # [DT-P3]
    _publishing(processed, "fx-stopped", os.getpid(), status="pending")
    assert client.get("/api/runs/fx-stopped").json()["actions"] == ["retry", "restart", "dismiss"]


def test_retry_real_argv_and_record(processed, spawned, tmp_path):  # [DT-F1]
    with sqlite3.connect(tmp_path / "ck.db") as db:
        db.execute("create table checkpoints (thread_id text)")
        db.execute("insert into checkpoints values ('fx-stopped')")
    db.close()
    client = client_for(processed, runner="real")
    set_rec(processed, "fx-stopped", dismissed=True)
    (rd_of(processed, "fx-stopped") / "pending.json").write_text("{}")
    r = client.post("/api/runs/fx-stopped/retry", json={})
    assert r.status_code == 202 and r.json() == {"run_id": "fx-stopped"}
    argv, log = spawned[0]
    assert argv == [sys.executable, "-m", "pipeline.retry", "--profile", "sandbox", "--run-id", "fx-stopped",
                    "--processed-root", str(processed), "--no-archive"]
    assert log == rd_of(processed, "fx-stopped") / "stdout.log"
    rec = rec_of(processed, "fx-stopped")
    assert rec["status"] == "running" and rec["pid"] == os.getpid() and rec["ended_at"] is None and rec["error"] is None
    assert rec["dismissed"] is False
    assert not (rd_of(processed, "fx-stopped") / "pending.json").exists()


def test_retry_auto_approve_passes_the_flag(processed, spawned, tmp_path):  # [DT-F1]
    with sqlite3.connect(tmp_path / "ck.db") as db:
        db.execute("create table checkpoints (thread_id text)")
        db.execute("insert into checkpoints values ('fx-stopped')")
    db.close()
    r = client_for(processed, runner="real").post("/api/runs/fx-stopped/retry", json={"auto_approve": True})
    assert r.status_code == 202 and spawned[0][0][-1] == "--auto-approve"


def test_bodies_are_optional(client, processed, spawned):  # [DT-F4] [DT-F7]
    assert client.post("/api/runs/fx-stopped/restart").status_code == 201
    assert client.post("/api/runs/fx-failed/dismiss").json() == {"dismissed": True}


def test_retry_chooses_the_fake_runner(client, processed, spawned):  # [DT-F1]
    assert client.post("/api/runs/fx-stopped/retry", json={}).status_code == 202
    assert spawned[0][0][:6] == [sys.executable, "-m", "pipeline.ui.fake_runner", "retry", "--run-id", "fx-stopped"]


def test_retry_refusals(client, processed, spawned):  # [DT-F6] [DT-S4]
    assert client.post("/api/runs/nope/retry", json={}).status_code == 404
    r = client.post("/api/runs/fx-nocheckpoint/retry", json={})
    assert r.status_code == 409 and "checkpoint" in r.json()["detail"]
    r = client.post("/api/runs/fx-done/retry", json={})
    assert r.status_code == 409 and r.json()["detail"]
    set_rec(processed, "fx-running", pid=os.getpid())
    r = client.post("/api/runs/fx-stopped/retry", json={})
    assert r.status_code == 409 and r.json()["run_id"] == "fx-running"
    assert spawned == []


def test_retry_spawn_failure_records_failed(client, processed, monkeypatch):  # [DT-F1]
    def boom(argv, log):
        raise OSError("no fork")

    monkeypatch.setattr(launcher, "spawn", boom)
    with pytest.raises(OSError):
        client.post("/api/runs/fx-stopped/retry", json={})
    rec = rec_of(processed, "fx-stopped")
    assert rec["status"] == "failed" and "no fork" in rec["error"]


def test_restart_launches_a_new_run_with_the_same_input(client, processed, spawned):  # [DT-F4]
    r = client.post("/api/runs/fx-stopped/restart", json={})
    assert r.status_code == 201
    new = r.json()["run_id"]
    assert new != "fx-stopped" and new.endswith("_ui")
    rec = rec_of(processed, new)
    assert rec["input"]["path"] == str(REPO / "tests/fixtures/ui/inbox/transcript_2026-10-30.txt")
    assert "--input" in spawned[0][0]
    assert rec_of(processed, "fx-stopped")["status"] == "stopped"


def test_restart_refusals(client, processed, spawned):  # [DT-F5] [DT-S4]
    assert client.post("/api/runs/nope/restart", json={}).status_code == 404
    r = client.post("/api/runs/fx-failed/restart", json={})
    assert r.status_code == 404 and "gone" in r.json()["detail"]
    set_rec(processed, "fx-running", pid=os.getpid())
    r = client.post("/api/runs/fx-stopped/restart", json={})
    assert r.status_code == 409 and r.json()["run_id"] == "fx-running"
    assert spawned == []


def test_restart_finds_an_archived_input(client, processed, spawned):  # [DT-F4]
    r = client.post("/api/runs/fx-legacy/restart", json={})
    assert r.status_code == 201
    assert rec_of(processed, r.json()["run_id"])["input"]["path"].endswith("source/PXL_20260515_201436156.mp4")


# --- log --------------------------------------------------------------------


def test_log_tail(client, processed):  # [DT-R1]
    rd = rd_of(processed, "fx-unpackaged")
    body = client.get("/api/runs/fx-unpackaged/log?tail=2").json()
    assert body["lines"] == ["[run] line 4", "[run] line 5"]
    assert body["bytes"] == (rd / "stdout.log").stat().st_size
    assert len(client.get("/api/runs/fx-unpackaged/log").json()["lines"]) == 5
    assert client.get("/api/runs/fx-done/log").json() == {"lines": [], "bytes": 0}
    assert client.get("/api/runs/nope/log").status_code == 404


def test_log_tail_of_a_large_file_is_bounded(client, processed, monkeypatch):  # [DT-R1]
    monkeypatch.setattr(actions, "LOG_READ_CAP", 100)
    (rd_of(processed, "fx-done") / "stdout.log").write_bytes(b"".join(b"line %d\n" % i for i in range(1000)))
    body = client.get("/api/runs/fx-done/log?tail=500").json()
    assert body["lines"][-1] == "line 999" and all(ln.startswith("line ") for ln in body["lines"])
    assert body["bytes"] > 100


# --- artifact PUT -----------------------------------------------------------


def _art(client, run, name):
    arts = client.get(f"/api/runs/{run}/artifacts").json()["artifacts"]
    return next(a for a in arts if a["name"] == name)


def test_artifact_put_saves_atomically(client, processed):  # [DT-D4]
    art = _art(client, "fx-unpackaged", "post.md")
    r = client.put(f"/api/runs/fx-unpackaged/artifacts/{art['id']}", content="edited é\n".encode())
    assert r.status_code == 200 and r.json() == {"bytes": len("edited é\n".encode())}
    assert client.get(f"/api/runs/fx-unpackaged/artifacts/{art['id']}").text == "edited é\n"
    assert not [p for p in (rd_of(processed, "fx-unpackaged") / "wordpress-draft").iterdir() if p.name.endswith(".tmp")]


def test_artifact_put_refusals(client, processed, monkeypatch):  # [DT-D4]
    png = _art(client, "fx-unpackaged", "featured.png")
    assert client.put(f"/api/runs/fx-unpackaged/artifacts/{png['id']}", content=b"x").status_code == 415
    txt = _art(client, "fx-unpackaged", "post.md")
    url = f"/api/runs/fx-unpackaged/artifacts/{txt['id']}"
    monkeypatch.setattr(artifacts, "TEXT_CAP", 4)
    assert client.put(url, content=b"12345").status_code == 413
    assert client.put(url, content=b"\xff\xfe\xfa").status_code == 400
    assert client.put("/api/runs/fx-unpackaged/artifacts/nope", content=b"x").status_code == 404
    assert client.put(f"/api/runs/nope/artifacts/{txt['id']}", content=b"x").status_code == 404
    pkg = _art(client, "fx-done", "package.json")
    assert client.put(f"/api/runs/fx-done/artifacts/{pkg['id']}", content=b"{}").status_code == 409
    set_rec(processed, "fx-unpackaged", status="running", pid=os.getpid())
    assert client.put(url, content=b"x").status_code == 409


# --- research ---------------------------------------------------------------


def test_research_items(client):  # [DT-D5]
    items = client.get("/api/runs/fx-unpackaged/research").json()["items"]
    assert [i["n"] for i in items] == [1, 2, 3, 4, 5, 6]
    first = items[0]
    assert {k: first[k] for k in ("title", "type", "author", "location", "category", "angle", "relevance", "paywall",
                                  "theme", "reachable", "reason")} == {
        "title": "Endure", "type": "Book", "author": "Alex Hutchinson", "location": "Chapter 4", "category": "Going Deeper",
        "angle": "limits", "relevance": "Why the mind quits first.", "paywall": "true", "theme": "endurance",
        "reachable": "yes", "reason": ""}
    assert first["link"] == {"kind": "http", "href": "https://example.org/endure"}
    assert items[1]["link"] == {"kind": "local", "href": "/api/runs/fx-unpackaged/research/2/file"}
    assert [items[i]["link"]["kind"] for i in (2, 3, 4, 5)] == ["none"] * 4
    assert items[2]["link"]["href"] is None


def test_research_short_csv_and_no_csv(client):  # [DT-D5]
    items = client.get("/api/runs/fx-done/research").json()["items"]
    assert items[0]["title"] == "Born to Run" and items[0]["link"] == {"kind": "http", "href": "https://example.org/a"}
    assert items[0]["author"] == ""
    assert client.get("/api/runs/fx-failed/research").json() == {"items": []}
    assert client.get("/api/runs/nope/research").status_code == 404


def test_research_local_file_route(client):  # [DT-D5]
    r = client.get("/api/runs/fx-unpackaged/research/2/file")
    assert r.status_code == 200 and r.text == "A tiny fake book.\n"
    assert r.headers["content-type"].startswith("text/plain")
    for n in (1, 3, 4, 5, 6, 7, 0, "x"):
        assert client.get(f"/api/runs/fx-unpackaged/research/{n}/file").status_code in {404, 422}, n


def test_research_symlink_out_of_the_library_is_refused(processed, tmp_path):  # [DT-D5]
    books = tmp_path / "books"
    books.mkdir()
    secret = tmp_path / "secret.txt"
    secret.write_text("no")
    (books / "link.txt").symlink_to(secret)
    (books / "real.txt").write_text("yes")
    (books / "sub").symlink_to(tmp_path)
    rd = rd_of(processed, "fx-done")
    (rd / "research-curator" / "research_report.csv").write_text(
        f"Title,Type,Source URL\nA,Book,file://{books}/link.txt\nB,Book,file://{books}/real.txt\n"
        f"C,Book,file://{books}/sub/secret.txt\nD,Book,file://{books}/../secret.txt\n")
    client = TestClient(create_app(Settings(processed_root=processed, inbox=FIX / "inbox", books_root=books)), base_url="http://127.0.0.1")
    links = [i["link"]["kind"] for i in client.get("/api/runs/fx-done/research").json()["items"]]
    assert links == ["none", "local", "none", "none"]
    assert client.get("/api/runs/fx-done/research/2/file").text == "yes"
    for n in (1, 3, 4):
        assert client.get(f"/api/runs/fx-done/research/{n}/file").status_code == 404


def test_research_html_book_is_not_rendered_inline(processed, tmp_path):  # [DT-D5]
    books = tmp_path / "books"
    books.mkdir()
    (books / "b.html").write_text("<script>alert(1)</script>")
    (rd_of(processed, "fx-done") / "research-curator" / "research_report.csv").write_text(
        f"Title,Type,Source URL\nA,Book,file://{books}/b.html\n")
    client = TestClient(create_app(Settings(processed_root=processed, inbox=FIX / "inbox", books_root=books)), base_url="http://127.0.0.1")
    assert client.get("/api/runs/fx-done/research/1/file").headers["content-type"] == "application/octet-stream"


# --- publish: overview, prepare, save, asset --------------------------------


def test_overview_always_has_targets_and_prepare(client, processed):  # [DT-P2] [DT-P7] [DT-P1]
    body = client.get("/api/runs/fx-unpackaged/publish").json()
    assert body["targets"] == {}
    assert body["prepare"] == {"youtube": {"can": True, "reason": None, "mode": "manual"},
                               "wordpress": {"can": True, "reason": None, "mode": "manual"}}
    failed = client.get("/api/runs/fx-failed/publish").json()
    assert failed["targets"] == {}
    assert failed["prepare"]["wordpress"]["can"] is False and "blog" in failed["prepare"]["wordpress"]["reason"]
    assert failed["prepare"]["youtube"]["can"] is False and "video" in failed["prepare"]["youtube"]["reason"]
    off = client.get("/api/runs/fx-modeoff/publish").json()["prepare"]["wordpress"]
    assert off["can"] is False and off["mode"] == "off" and "off" in off["reason"]
    done = client.get("/api/runs/fx-done/publish").json()
    assert done["targets"]["wordpress"]["status"] == "pending"
    assert done["prepare"]["wordpress"]["can"] is False and done["prepare"]["wordpress"]["reason"]


def test_prepare_wordpress_from_the_bundle(client, processed):  # [DT-P2]
    r = client.post("/api/runs/fx-unpackaged/publish/wordpress/prepare")
    assert r.status_code == 201
    pkg = r.json()
    assert pkg["status"] == "pending" and pkg["target"] == "wordpress" and pkg["mode"] == "manual"
    assert pkg["fields"]["title"] == "The road stopped arguing"
    assert pkg["fields"]["body"].startswith("I ran until")
    assert pkg["fields"]["tags"] == ["painforwisdom", "endurance"] and pkg["fields"]["status"] == "draft"
    assert pkg["readonly"]["date_mmddyy"] == "10/28/26"
    assert pkg["assets"][0]["role"] == "featured_image" and pkg["assets"][0]["path"].endswith("featured.png")
    assert set(pkg["field_specs"]) == set(pkg["fields"])
    assert client.get("/api/runs/fx-unpackaged/publish").json()["targets"]["wordpress"]["status"] == "pending"
    assert client.post("/api/runs/fx-unpackaged/publish/wordpress/prepare").status_code == 409


def test_prepare_wordpress_falls_back_to_the_writer_output(client, processed):  # [DT-P2]
    rd = rd_of(processed, "fx-unpackaged")
    shutil.rmtree(rd / "wordpress-draft")
    pkg = client.post("/api/runs/fx-unpackaged/publish/wordpress/prepare").json()
    assert pkg["fields"]["title"] == "The road stopped arguing"
    assert "**Title:**" not in pkg["fields"]["body"] and pkg["fields"]["body"].startswith("I ran until")
    assert pkg["fields"]["excerpt"].startswith("I ran until") and pkg["assets"] == []


def test_prepare_youtube_from_the_metadata_and_input(client):  # [DT-P2]
    r = client.post("/api/runs/fx-unpackaged/publish/youtube/prepare")
    assert r.status_code == 201
    pkg = r.json()
    assert pkg["fields"]["title"] == "When the road stops arguing" and pkg["fields"]["kind"] == "short"
    assert pkg["fields"]["privacy_status"] == "private"
    assert pkg["assets"] == [{"role": "video", "path": str(REPO / "tests/fixtures/ui/inbox/PXL_20261030_070000000.mp4")}]


def test_prepare_youtube_with_empty_metadata_starts_from_the_blog(client, processed):  # [DT-P2]
    # Real case: a run from before the metadata node has title "" and description "".
    meta = processed / "fx-unpackaged" / "langgraph" / "youtube-upload" / "metadata.json"
    meta.write_text(json.dumps({"title": "", "description": "", "tags": ["painforwisdom"]}))
    pkg = client.post("/api/runs/fx-unpackaged/publish/youtube/prepare").json()
    assert pkg["fields"]["title"] == "The road stopped arguing"
    assert pkg["fields"]["description"].startswith("I ran until the road stopped arguing")
    assert "blog post" in pkg["readonly"]["warning"]


def test_prepare_youtube_with_metadata_has_no_warning(client):  # [DT-P2]
    assert "warning" not in client.post("/api/runs/fx-unpackaged/publish/youtube/prepare").json()["readonly"]


def test_prepare_youtube_uses_the_archive_when_input_moved(client, processed):  # [DT-P2]
    set_rec(processed, "fx-unpackaged", input={"kind": "video", "path": "/nonexistent/a.mp4"})
    source = processed / "fx-unpackaged" / "source"
    source.mkdir()
    (source / "a.mp4").write_bytes(b"\0")
    assert client.post("/api/runs/fx-unpackaged/publish/youtube/prepare").json()["assets"][0]["path"] == str(source / "a.mp4")


def test_prepare_refusals(client, processed):  # [DT-P1] [DT-P7]
    r = client.post("/api/runs/fx-modeoff/publish/wordpress/prepare")
    assert r.status_code == 422 and "off" in r.json()["detail"]
    r = client.post("/api/runs/fx-failed/publish/youtube/prepare")
    assert r.status_code == 422 and "video" in r.json()["detail"]
    r = client.post("/api/runs/fx-failed/publish/wordpress/prepare")
    assert r.status_code == 422 and "blog" in r.json()["detail"]
    assert client.post("/api/runs/fx-unpackaged/publish/nope/prepare").status_code == 404
    assert client.post("/api/runs/nope/publish/wordpress/prepare").status_code == 404
    assert client.post("/api/runs/fx-done/publish/wordpress/prepare").status_code == 409
    set_rec(processed, "fx-unpackaged", status="running", pid=os.getpid())
    assert client.post("/api/runs/fx-unpackaged/publish/wordpress/prepare").status_code == 409


def test_transcript_run_cannot_prepare_a_video(client):  # [DT-P7]
    assert client.post("/api/runs/fx-stopped/publish/youtube/prepare").status_code == 422


def test_save_without_publishing(client, processed):  # [DT-P3]
    r = client.put("/api/runs/fx-done/publish/wordpress", json={"fields": {"title": "", "excerpt": "new"}})
    assert r.status_code == 200  # not `required`: a draft may be incomplete
    pkg = r.json()
    assert pkg["status"] == "pending" and pkg["fields"]["title"] == "" and pkg["fields"]["excerpt"] == "new"
    assert pkg["fields"]["body"]  # untouched
    assert client.get("/api/runs/fx-done/publish").json()["targets"]["wordpress"]["fields"]["excerpt"] == "new"
    assert not (rd_of(processed, "fx-done") / "wordpress-draft" / "publish.lock").exists()


def test_save_validates_types_and_max(client):  # [DT-P8]
    r = client.put("/api/runs/fx-done/publish/wordpress", json={"fields": {"title": "x" * 201, "tags": "no", "zzz": 1}})
    assert r.status_code == 400
    assert set(r.json()["detail"]) == {"title: over max 200", "tags: must be a list of strings", "zzz: unknown field"}


def test_save_keeps_a_failed_package_failed(client, processed):  # [DT-P5]
    path = rd_of(processed, "fx-done") / "wordpress-draft" / "package.json"
    path.write_text(json.dumps({**json.loads(path.read_text()), "status": "failed", "error": "boom"}))
    pkg = client.put("/api/runs/fx-done/publish/wordpress", json={"fields": {"title": "T2"}}).json()
    assert pkg["status"] == "failed" and pkg["error"] == "boom" and pkg["fields"]["title"] == "T2"


def test_save_refusals(client, processed):  # [DT-P6] [DT-P9]
    assert client.put("/api/runs/fx-published/publish/wordpress", json={"fields": {}}).status_code == 409
    assert client.put("/api/runs/fx-unpackaged/publish/wordpress", json={"fields": {}}).status_code == 404
    assert client.put("/api/runs/fx-done/publish/nope", json={"fields": {}}).status_code == 404
    assert client.put("/api/runs/nope/publish/wordpress", json={"fields": {}}).status_code == 404
    rd = rd_of(processed, "fx-done")
    (rd / "wordpress-draft" / "publish.lock").write_text(str(os.getpid()))
    r = client.put("/api/runs/fx-done/publish/wordpress", json={"fields": {"title": "T"}})
    assert r.status_code == 409 and r.json()["detail"] == "publishing"


def test_published_then_save_is_read_only(client, processed, spawned):  # [DT-P4] [DT-P6]
    path = rd_of(processed, "fx-done") / "wordpress-draft" / "package.json"
    path.write_text(json.dumps({**json.loads(path.read_text()), "status": "publishing"}))
    (path.parent / "publish.lock").write_text(str(os.getpid()))
    assert client.put("/api/runs/fx-done/publish/wordpress", json={"fields": {"title": "T"}}).status_code == 409


def test_asset_route_serves_listed_files_with_ranges(client):  # [DT-D3] [DT-P3]
    assert client.post("/api/runs/fx-unpackaged/publish/youtube/prepare").status_code == 201
    size = (FIX / "inbox" / "PXL_20261030_070000000.mp4").stat().st_size
    r = client.get("/api/runs/fx-unpackaged/publish/youtube/asset/video")
    assert r.status_code == 200 and r.headers["content-type"] == "video/mp4" and len(r.content) == size
    r = client.get("/api/runs/fx-unpackaged/publish/youtube/asset/video", headers={"Range": "bytes=0-0"})
    assert r.status_code == 206 and len(r.content) == 1
    assert client.post("/api/runs/fx-unpackaged/publish/wordpress/prepare").status_code == 201
    r = client.get("/api/runs/fx-unpackaged/publish/wordpress/asset/featured_image")
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"


def test_asset_route_404s(client):  # [DT-D3]
    assert client.get("/api/runs/fx-unpackaged/publish/youtube/asset/video").status_code == 404  # no package
    assert client.get("/api/runs/fx-done/publish/wordpress/asset/nope").status_code == 404
    assert client.get("/api/runs/fx-done/publish/wordpress/asset/featured_image").status_code == 404  # listed, file gone
    assert client.get("/api/runs/nope/publish/wordpress/asset/featured_image").status_code == 404
    assert client.get("/api/runs/fx-done/publish/nope/asset/featured_image").status_code == 404


def test_auto_mode_failure_can_be_published_again(client, processed, spawned):  # [DT-P10] [DT-P5]
    path = rd_of(processed, "fx-done") / "wordpress-draft" / "package.json"
    pkg = json.loads(path.read_text())
    path.write_text(json.dumps({**pkg, "mode": "auto", "status": "failed", "error": "boom"}))
    got = client.get("/api/runs/fx-done/publish").json()["targets"]["wordpress"]
    assert got["mode"] == "auto" and got["status"] == "failed"
    assert client.post("/api/runs/fx-done/publish/wordpress", json={"fields": pkg["fields"]}).status_code == 202


def test_dead_publisher_shows_failed_then_publishes_again(client, processed, spawned):  # [DT-P11]
    rd = rd_of(processed, "fx-done")
    path = rd / "wordpress-draft" / "package.json"
    pkg = json.loads(path.read_text())
    path.write_text(json.dumps({**pkg, "status": "publishing"}))
    (rd / "wordpress-draft" / "publish.lock").write_text("99999999")
    got = client.get("/api/runs/fx-done/publish").json()["targets"]["wordpress"]
    assert got["status"] == "failed" and got["error"] == publish.PUBLISHER_DIED
    assert client.post("/api/runs/fx-done/publish/wordpress", json={"fields": pkg["fields"]}).status_code == 202


def test_publish_after_prepare_runs_through_the_fake_publisher(client, processed, spawned):  # [DT-P4]
    pkg = client.post("/api/runs/fx-unpackaged/publish/wordpress/prepare").json()
    r = client.post("/api/runs/fx-unpackaged/publish/wordpress", json={"fields": pkg["fields"]})
    assert r.status_code == 202 and r.json()["status"] == "publishing"
    assert publish.package.lock_path(rd_of(processed, "fx-unpackaged"), "wordpress").exists()


# --- inputs ------------------------------------------------------------------


def test_inputs_name_their_previous_runs(client):  # [DT-S3]
    inputs = {i["name"]: i for i in client.get("/api/inputs").json()["inputs"]}
    prev = inputs["transcript_2026-10-30.txt"]["previous_runs"]
    assert {"fx-done", "fx-stopped", "fx-failed"} <= set(prev)
    assert "fx-unpackaged" in inputs["PXL_20261030_070000000.mp4"]["previous_runs"]


def test_inputs_with_no_runs(tmp_path, processed):  # [DT-S2]
    inbox = tmp_path / "in"
    inbox.mkdir()
    (inbox / "new.txt").write_text("x")
    client = TestClient(create_app(Settings(processed_root=processed, inbox=inbox)), base_url="http://127.0.0.1")
    assert client.get("/api/inputs").json()["inputs"][0]["previous_runs"] == []


# --- secrets -----------------------------------------------------------------


def test_the_console_server_imports_no_secret_reading_client():
    code = ("import sys, pipeline.ui.app;"
            "from pipeline.ui import actions, publish, research, routes_actions, routes_publish;"
            "bad = {'pipeline.wordpress_client', 'pipeline.notion_client', 'pipeline.youtube_client',"
            " 'pipeline.publish.wordpress', 'pipeline.publish.youtube', 'pipeline.nodes.wordpress_draft',"
            " 'pipeline.nodes.youtube_upload'} & set(sys.modules);"
            "sys.exit(str(bad) if bad else 0)")
    out = subprocess.run([sys.executable, "-c", code], cwd=REPO, capture_output=True, text=True,
                         env={**os.environ, "CHECKPOINT_DB_PATH": "/tmp/pfw-unused-ck.db"})
    assert out.returncode == 0, out.stderr


def test_specs_are_stdlib_and_the_nodes_share_them():
    code = ("import sys, pipeline.publish.specs as s;"
            "bad = {'httpx', 'dotenv', 'litellm', 'langgraph'} & set(sys.modules);"
            "sys.exit(str(bad) if bad else 0)")
    out = subprocess.run([sys.executable, "-c", code], cwd=REPO, capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    from pipeline.nodes import wordpress_draft, youtube_metadata, youtube_upload
    from pipeline.publish import specs

    assert wordpress_draft.FIELD_SPECS is specs.WORDPRESS and youtube_upload.FIELD_SPECS is specs.YOUTUBE
    assert youtube_metadata.TITLE_MAX == specs.YOUTUBE["title"]["max"]


def test_config_path_honours_the_env():
    code = "from pipeline import publishing_config as c; print(c.CONFIG_PATH)"
    out = subprocess.run([sys.executable, "-c", code], cwd=REPO, capture_output=True, text=True,
                         env={**os.environ, "PIPELINE_PUBLISHING_CONFIG": "/tmp/x.json"})
    assert out.stdout.strip() == "/tmp/x.json"


def test_books_root_settings(monkeypatch):
    from pipeline import runtime

    s = Settings.from_env_and_args([], env={})
    assert s.books_root == runtime.STATE_ROOT / "books"
    assert Settings.from_env_and_args([], env={"PIPELINE_UI_BOOKS_ROOT": "/b"}).books_root == Path("/b")


def test_stop_guard_allowlist():  # [DT-R3]
    assert actions.is_runner(os.getpid()) is False
    assert actions.is_runner(99999999) is False
