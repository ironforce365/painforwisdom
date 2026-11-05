"""pipeline.retry keeps run.json true and uses the C11 mailbox (spec 2026-11-05-console-end-to-end sections 1, 3)."""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List

import pytest

os.environ.setdefault("CHECKPOINT_DB_PATH", str(Path(tempfile.mkdtemp()) / "checkpoints.db"))

from langgraph.checkpoint.memory import InMemorySaver  # noqa: E402
from langgraph.graph import END, START, StateGraph  # noqa: E402
from langgraph.types import interrupt  # noqa: E402

from pipeline import retry  # noqa: E402
from pipeline.state import State  # noqa: E402

RID = "rid"
CONFIG = {"configurable": {"thread_id": RID}}


def _graph(fn):
    g = StateGraph(State)
    g.add_node("n", fn)
    g.add_edge(START, "n")
    g.add_edge("n", END)
    return g.compile(checkpointer=InMemorySaver())


def _args(tmp_path: Path, **kw) -> argparse.Namespace:
    base = dict(processed_root=tmp_path / "processed", run_suffix="langgraph", auto_approve=False,
                telegram_on_error=False, reminder_interval=1, dry_run=False, no_archive=False)
    return argparse.Namespace(**{**base, **kw})


def _rd(tmp_path: Path) -> Path:
    return tmp_path / "processed" / RID / "langgraph"


def _seed(tmp_path: Path, **fields) -> None:
    _rd(tmp_path).mkdir(parents=True)
    rec = {"run_id": RID, "status": "failed", "verdict": None, "pid": 1, "profile": "sandbox",
           "input": {"kind": "transcript", "path": "/x/t.txt"}, "started_at": 5.0, "ended_at": 9.0, "hitl_wait_s": 2.0,
           "error": "RuntimeError: first", **fields}
    (_rd(tmp_path) / "run.json").write_text(json.dumps(rec))


def _rec(tmp_path: Path) -> Dict[str, Any]:
    return json.loads((_rd(tmp_path) / "run.json").read_text())


@pytest.fixture(autouse=True)
def _no_telegram(monkeypatch):
    monkeypatch.setattr(retry, "telegram_send", lambda msg: 0)


def _failing_first(fails: int = 1, **ok):
    calls = {"n": 0}

    def node(state):
        calls["n"] += 1
        if calls["n"] <= fails:
            raise RuntimeError(f"boom {calls['n']}")
        return {"validator_verdict": "PASS", **ok}

    return node, calls


def _started(graph) -> None:
    with pytest.raises(RuntimeError):
        graph.invoke({"run_dir": "x"}, config=CONFIG)


def test_success_updates_run_json(tmp_path):  # [DT-F1]
    node, _ = _failing_first()
    graph = _graph(node)
    _started(graph)
    _seed(tmp_path)
    seen: List[Dict[str, Any]] = []
    graph_invoke = graph.invoke

    def spy(*a, **k):
        seen.append(_rec(tmp_path))
        return graph_invoke(*a, **k)

    graph.invoke = spy  # type: ignore[method-assign]
    assert retry._retry_one(graph, RID, video=None, args=_args(tmp_path)) == 0
    live = seen[0]
    assert live["status"] == "running" and live["pid"] == os.getpid()
    assert live["ended_at"] is None and live["error"] is None and live["verdict"] is None
    rec = _rec(tmp_path)
    assert rec["status"] == "done" and rec["verdict"] == "PASS" and rec["ended_at"] > 9.0 and rec["error"] is None
    assert rec["started_at"] == 5.0 and rec["input"]["path"] == "/x/t.txt"


def test_second_failure_asks_again_then_retries(tmp_path, monkeypatch):  # [DT-E2]
    node, _ = _failing_first(fails=2)
    graph = _graph(node)
    _started(graph)
    _seed(tmp_path)
    asks: List[Dict[str, Any]] = []

    def fake_ask(rd, request, interval, max_reminders=None):
        asks.append({"status": _rec(tmp_path)["status"], "request": request, "max": max_reminders, "rd": rd})
        return "retry"

    monkeypatch.setattr(retry.hitl, "ask", fake_ask)
    assert retry._retry_one(graph, RID, video=None, args=_args(tmp_path)) == 0
    assert len(asks) == 1
    assert asks[0]["status"] == "waiting_reply" and asks[0]["request"]["kind"] == "error" and asks[0]["max"] is None
    assert asks[0]["rd"] == _rd(tmp_path)
    assert _rec(tmp_path)["status"] == "done"


def test_abort_marks_failed_with_the_error(tmp_path, monkeypatch):  # [DT-E3]
    node, _ = _failing_first(fails=9)
    graph = _graph(node)
    _started(graph)
    _seed(tmp_path)
    monkeypatch.setattr(retry.hitl, "ask", lambda *a, **k: "abort")
    assert retry._retry_one(graph, RID, video=None, args=_args(tmp_path)) == 2
    rec = _rec(tmp_path)
    assert rec["status"] == "failed" and rec["error"] == "RuntimeError: boom 2" and rec["ended_at"] > 9.0


def test_non_recoverable_on_retry_offers_only_abort(tmp_path, monkeypatch):  # [DT-E4]
    def node(state):
        raise RuntimeError("Invalid authentication credentials")

    graph = _graph(node)
    _started(graph)
    _seed(tmp_path)
    seen: List[Dict[str, Any]] = []
    monkeypatch.setattr(retry.hitl, "ask", lambda rd, req, *a, **k: seen.append(req) or "retry")
    assert retry._retry_one(graph, RID, video=None, args=_args(tmp_path)) == 2
    assert [o["reply"] for o in seen[0]["options"]] == ["abort"]
    assert _rec(tmp_path)["status"] == "failed"


def test_hitl_approval_goes_through_the_mailbox(tmp_path, monkeypatch):  # [DT-A1]
    def node(state):
        answer = interrupt({"kind": "theme", "proposal": "p"})
        return {"validator_verdict": "PASS", "summary_report_path": answer}

    graph = _graph(node)
    graph.invoke({"run_dir": "x"}, config=CONFIG)
    _seed(tmp_path, hitl_wait_s=2.0)
    seen: List[Any] = []

    def fake_ask(rd, request, interval, max_reminders=None):
        seen.append((_rec(tmp_path)["status"], request["kind"], request["node"], request["title"], max_reminders))
        return "yes"

    monkeypatch.setattr(retry.hitl, "ask", fake_ask)
    assert retry._retry_one(graph, RID, video=None, args=_args(tmp_path)) == 0
    assert seen == [("waiting_hitl", "approval", "n", "New theme: approval needed", None)]
    rec = _rec(tmp_path)
    assert rec["status"] == "done" and rec["hitl_wait_s"] >= 2.0


def test_no_checkpoint_fails_the_record(tmp_path):  # [DT-F6]
    graph = _graph(_failing_first()[0])
    _seed(tmp_path)
    assert retry._retry_one(graph, RID, video=None, args=_args(tmp_path)) == 2
    rec = _rec(tmp_path)
    assert rec["status"] == "failed" and "checkpoint" in rec["error"]


def test_terminal_checkpoint_records_done(tmp_path):  # [DT-F1]
    graph = _graph(lambda state: {"validator_verdict": "PARTIAL"})
    graph.invoke({"run_dir": "x"}, config=CONFIG)
    _seed(tmp_path)
    assert retry._retry_one(graph, RID, video=None, args=_args(tmp_path)) == 0
    rec = _rec(tmp_path)
    assert rec["status"] == "done" and rec["verdict"] == "PARTIAL" and rec["error"] is None


def test_dry_run_and_legacy_runs_leave_no_record(tmp_path):
    node, _ = _failing_first()
    graph = _graph(node)
    _started(graph)
    assert retry._retry_one(graph, RID, video=None, args=_args(tmp_path, dry_run=True)) == 0
    assert not (_rd(tmp_path) / "run.json").exists()
    _rd(tmp_path).mkdir(parents=True)
    assert retry._retry_one(graph, RID, video=None, args=_args(tmp_path)) == 0  # the old CLI path: no run.json, none made
    assert not (_rd(tmp_path) / "run.json").exists()


def test_sigterm_style_exit_leaves_failed_and_reraises(tmp_path, monkeypatch):  # [DT-R3]
    node, _ = _failing_first()
    graph = _graph(node)
    _started(graph)
    _seed(tmp_path)

    def stopped(*a, **k):
        raise SystemExit(143)

    monkeypatch.setattr(retry.hitl, "ask", stopped)
    monkeypatch.setattr(retry, "_resume_graph", stopped)
    with pytest.raises(SystemExit):
        retry._retry_one(graph, RID, video=None, args=_args(tmp_path))
    assert _rec(tmp_path)["status"] == "failed"


def test_archive_uses_processed_root_unless_no_archive(tmp_path):  # [DT-F1]
    for flag, archived in ((True, False), (False, True)):
        video = tmp_path / f"v{flag}.mp4"
        video.write_text("v")
        node, _ = _failing_first()
        graph = _graph(node)
        _started(graph)
        assert retry._retry_one(graph, RID, video=video, args=_args(tmp_path, no_archive=flag)) == 0
        assert video.is_file() is not archived
        assert (tmp_path / "processed" / RID / "source" / video.name).is_file() is archived


def test_main_parses_processed_root_and_no_archive(tmp_path, monkeypatch):  # [DT-F1]
    node, _ = _failing_first()
    graph = _graph(node)
    _started(graph)
    _seed(tmp_path)

    class _Saver:
        class conn:  # noqa: N801
            @staticmethod
            def close():
                pass

    monkeypatch.setattr(retry, "build_graph", lambda start_at: (graph, _Saver))
    monkeypatch.setattr(sys, "argv", ["retry", "--run-id", RID, "--processed-root", str(tmp_path / "processed"),
                                      "--no-archive"])
    assert retry.main() == 0
    assert _rec(tmp_path)["status"] == "done"
