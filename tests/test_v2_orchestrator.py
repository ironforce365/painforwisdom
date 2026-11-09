"""pipeline.v2.orchestrator with stub nodes and FakeSession (spec 2026-11-09-session-pipeline-v2 §5.5)."""
from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, Callable, Dict, List

import pytest

from pipeline import graph, hitl, llm, usage
from pipeline.v2 import orchestrator as orch_mod
from pipeline.v2 import session
from pipeline.v2.orchestrator import TOPOLOGY, Orchestrator
from pipeline.v2.store import Store

THEME = {"action": "NEEDS_APPROVAL_THEME", "proposed_theme": "t"}


class World:
    def __init__(self, tmp_path: Path, monkeypatch):
        self.run_dir = tmp_path / "run"
        self.run_dir.mkdir()
        self.store = Store(tmp_path / "v2.db")
        self.ran: List[str] = []
        self.seen: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()
        self.mp = monkeypatch
        self.asked: List[Dict[str, Any]] = []
        self.replies: List[str] = []
        monkeypatch.setattr(orch_mod, "_sleep", lambda s: None)
        monkeypatch.setattr(orch_mod, "telegram_send", lambda *a, **k: 0)
        outs: Dict[str, Dict[str, Any]] = {n: {} for n in TOPOLOGY}
        outs.update({
            "extract": {"extraction_report": "r", "metrics": [{"stage": "extract"}]},
            "writer": {"blog_post_title": "t", "blog_post_path": str(tmp_path / "post.md"), "metrics": [{"stage": "writer"}]},
            "bv_research": {"branch_validations_done": ["research"]},
            "bv_wordpress": {"branch_validations_done": ["wordpress"]},
            "bv_youtube": {"branch_validations_done": ["youtube"]},
        })
        (tmp_path / "post.md").write_text("post")
        for name, out in outs.items():
            self.node(name, lambda state, out=out: out)
        self.node("summarizer", lambda s: {"validator_verdict": "PASS"} if len(s["branch_validations_done"]) == 3 else {})
        monkeypatch.setattr(orch_mod, "curate", lambda state, ask: self._called("kb_curator", state) or {})

        def ask(run_dir, request, interval, max_reminders=None):
            self.asked.append({**request, "status": json.loads((self.run_dir / "run.json").read_text())["status"]})
            return self.replies.pop(0)

        monkeypatch.setattr(hitl, "ask", ask)
        (self.run_dir / "run.json").write_text(json.dumps({"status": "running"}))

    def _called(self, name: str, state: Dict[str, Any]) -> None:
        with self._lock:
            self.ran.append(name)
            self.seen[name] = state

    def node(self, name: str, fn: Callable[[Dict[str, Any]], Dict[str, Any]]) -> None:
        def wrapped(state):
            self._called(name, state)
            return fn(state)

        self.mp.setattr(graph, f"node_{name}", wrapped)

    def orch(self, **kw: Any) -> Orchestrator:
        return Orchestrator("r", self.run_dir, self.store, reminder_interval=1, **kw)

    def state(self) -> Dict[str, Any]:
        return {"run_id": "r", "run_dir": str(self.run_dir), "metrics": []}

    def rows(self, sql: str) -> List[tuple]:
        return sqlite3.connect(self.store.path).execute(sql).fetchall()

    def events(self, node: str) -> List[str]:
        rows = [json.loads(l) for l in (self.run_dir / "events.jsonl").read_text().splitlines()]
        return [r["event"] for r in rows if r["node"] == node]


@pytest.fixture
def world(tmp_path, monkeypatch) -> World:
    return World(tmp_path, monkeypatch)


def test_topology_order_joins_once_and_reducers(world):
    final = world.orch().walk(world.state(), "extract")
    assert sorted(world.ran) == sorted(n for n in TOPOLOGY if n != "transcribe")
    for node, preds in TOPOLOGY.items():
        for p in preds:
            if node in world.ran and p in world.ran:
                assert world.ran.index(p) < world.ran.index(node), (p, node)
    assert world.ran.count("wordpress_draft") == 1 and world.ran.count("summarizer") == 1
    assert sorted(world.seen["summarizer"]["branch_validations_done"]) == ["research", "wordpress", "youtube"]
    assert final["validator_verdict"] == "PASS"
    assert [m["stage"] for m in final["metrics"]] == ["extract", "writer"]


def test_store_rows_and_artifacts(world):
    world.orch().walk(world.state(), "extract")
    steps = dict(world.rows("SELECT node, status FROM steps"))
    assert steps["writer"] == "ok" and steps["pre_validator"] == "noop" and len(steps) == len(TOPOLOGY) - 1
    assert world.rows("SELECT path, bytes FROM artifacts") == [(str(world.run_dir.parent / "post.md"), 4)]
    assert world.events("writer") == ["start", "end"]


def test_sessions_bound_in_worker_and_recorded(world, monkeypatch):
    fake = session.FakeSession("extracted")
    monkeypatch.setattr(session, "run_session", fake)
    bound: List[Any] = []
    world.node("extract", lambda s: bound.append(usage._BOUND.get()) or {
        "extraction_report": llm.call_llm("m", "sys", "user", max_tokens=50)["text"]})
    final = world.orch().walk(world.state(), "extract")
    assert final["extraction_report"] == "extracted"
    assert bound == [(str(world.run_dir), "extract", 1)]  # usage.jsonl rows land on this node
    assert fake.calls[0]["cwd"] == str(world.run_dir) and fake.calls[0]["allowed_tools"] == ()
    row = world.rows("SELECT s.system_prompt, s.text, s.max_tokens, st.node FROM sessions s JOIN steps st ON st.id = s.step_id")
    assert row == [("sys", "extracted", 50, "extract")]


def _flaky(exc: Exception, times: int, out: Dict[str, Any]):
    calls = {"n": 0}

    def fn(state):
        calls["n"] += 1
        if calls["n"] <= times:
            raise exc
        return out

    return fn


def test_transient_error_retried_on_v1_retried_nodes(world):
    world.node("extract", _flaky(ConnectionError("blip"), 2, {"extraction_report": "r"}))
    world.orch(auto_approve=True).walk(world.state(), "extract")
    assert world.rows("SELECT attempt, status FROM steps WHERE node='extract' ORDER BY id") == [
        (1, "error"), (2, "error"), (3, "ok")]
    assert world.events("extract") == ["start", "error", "start", "error", "start", "end"]


@pytest.mark.parametrize("node,exc", [("extract_image", ConnectionError("blip")), ("research", ValueError("bad"))])
def test_never_retried_aborts_under_auto_approve(world, monkeypatch, node, exc):
    sent: List[str] = []
    monkeypatch.setattr(orch_mod, "telegram_send", lambda text: sent.append(text) or 0)
    world.node(node, _flaky(exc, 9, {}))
    with pytest.raises(type(exc)):
        world.orch(auto_approve=True, telegram_on_error=True).walk(world.state(), "extract")
    assert world.rows(f"SELECT count(*) FROM steps WHERE node='{node}'") == [(1,)]
    assert len(sent) == 1 and node in sent[0]
    assert "summarizer" not in world.ran


def test_error_ask_retry_reruns_the_node(world):
    world.node("writer", _flaky(ValueError("bad output"), 1, {"blog_post_title": "t"}))
    world.replies = ["retry"]
    world.orch().walk(world.state(), "extract")
    assert [(a["kind"], a["node"], a["status"]) for a in world.asked] == [("error", "writer", "waiting_reply")]
    assert world.rows("SELECT kind, reply FROM hitl") == [("error", "retry")]
    assert world.rows("SELECT status FROM steps WHERE node='writer' ORDER BY id") == [("error",), ("ok",)]
    assert json.loads((world.run_dir / "run.json").read_text())["status"] == "running"


def test_error_ask_abort_raises(world):
    world.node("writer", _flaky(ValueError("bad output"), 1, {}))
    world.replies = ["abort"]
    with pytest.raises(ValueError):
        world.orch().walk(world.state(), "extract")
    assert world.rows("SELECT kind, reply FROM hitl") == [("error", "abort")]


def _curator(world, asks: int):
    def fake(state, ask):
        world._called("kb_curator", state)
        return {"replies": [ask({"kind": "theme", "proposal": f"p{i}", "raw_plan": THEME}) for i in range(asks)]}

    world.mp.setattr(orch_mod, "curate", fake)


def test_approval_pauses_records_and_returns_reply(world):
    _curator(world, 1)
    world.replies = ["yes"]
    o = world.orch()
    final = o.walk(world.state(), "extract")
    assert final["replies"] == ["yes"]
    assert [(a["kind"], a["node"], a["status"]) for a in world.asked] == [("approval", "kb_curator", "waiting_hitl")]
    assert world.events("kb_curator") == ["start", "paused", "start", "end"]
    assert world.rows("SELECT h.kind, h.reply, s.node FROM hitl h JOIN steps s ON s.id = h.step_id") == [
        ("approval", "yes", "kb_curator")]
    assert json.loads((world.run_dir / "run.json").read_text())["hitl_wait_s"] == o.hitl_wait


def test_auto_approve_says_yes_without_asking(world):
    _curator(world, 2)
    final = world.orch(auto_approve=True).walk(world.state(), "extract")
    assert final["replies"] == ["yes", "yes"] and world.asked == []
    assert world.rows("SELECT reply FROM hitl") == [("yes",), ("yes",)]


def test_auto_approve_refuses_a_curator_decision(world):
    def fake(state, ask):
        return {"r": ask({"kind": "curator", "proposal": "already processed"})}

    world.mp.setattr(orch_mod, "curate", fake)
    with pytest.raises(RuntimeError, match="--auto-approve cannot give one"):
        world.orch(auto_approve=True).walk(world.state(), "extract")
