"""Run observability: events.jsonl (C1) and run.json (C2).

Spec: docs/superpowers/specs/2026-10-31-pipeline-console-ui.md section 2.
No network, no LLM: stub nodes on tiny graphs, Telegram stubbed.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

# pipeline.graph reads CHECKPOINT_DB_PATH at import time.
os.environ.setdefault("CHECKPOINT_DB_PATH", str(Path(tempfile.mkdtemp()) / "checkpoints.db"))

from typing_extensions import TypedDict  # noqa: E402

from langgraph.checkpoint.memory import InMemorySaver  # noqa: E402
from langgraph.graph import END, START, StateGraph  # noqa: E402
from langgraph.types import Command, RetryPolicy, interrupt  # noqa: E402

from pipeline import graph as pgraph  # noqa: E402
from pipeline import run  # noqa: E402
from pipeline.state import State  # noqa: E402


class S(TypedDict, total=False):
    run_dir: str
    out_path: str
    other: str


def _events(run_dir: Path) -> List[Dict[str, Any]]:
    p = run_dir / "events.jsonl"
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_text().splitlines()]


def _seq(run_dir: Path, node: str) -> List[tuple]:
    return [(e["event"], e["attempt"]) for e in _events(run_dir) if e["node"] == node]


def _one_node_graph(fn, retry: Optional[RetryPolicy] = None, saver=None):
    g = StateGraph(S)
    g.add_node("n", pgraph._instrumented("n", fn), retry_policy=retry)
    g.add_edge(START, "n")
    g.add_edge("n", END)
    return g.compile(checkpointer=saver)


_FAST_RETRY = RetryPolicy(initial_interval=0.0, jitter=False, max_attempts=3, retry_on=lambda e: isinstance(e, ConnectionError))


# --- events.jsonl ------------------------------------------------------------


def test_success_writes_start_end_with_outputs(tmp_path):
    def node_ok(state):
        return {"out_path": "/x/out.md", "other": "v"}

    _one_node_graph(node_ok).invoke({"run_dir": str(tmp_path)})
    ev = _events(tmp_path)
    assert [(e["event"], e["attempt"]) for e in ev] == [("start", 1), ("end", 1)]
    end = ev[1]
    assert end["node"] == "n"
    assert end["noop"] is False
    assert end["outputs"] == {"out_path": "/x/out.md"}
    assert isinstance(end["duration_s"], float) and end["duration_s"] >= 0
    assert isinstance(ev[0]["ts"], float)
    assert set(ev[0]) == {"ts", "node", "event", "attempt"}
    assert set(end) == {"ts", "node", "event", "attempt", "duration_s", "noop", "outputs"}


def test_empty_return_is_noop(tmp_path):
    def node_noop(state):
        return {}

    _one_node_graph(node_noop).invoke({"run_dir": str(tmp_path)})
    end = _events(tmp_path)[-1]
    assert end["event"] == "end" and end["noop"] is True and end["outputs"] == {}


def test_transient_error_retried_by_policy(tmp_path):
    calls = {"n": 0}

    def node_flaky(state):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ConnectionError("blip")
        return {"out_path": "/x/o"}

    _one_node_graph(node_flaky, retry=_FAST_RETRY).invoke({"run_dir": str(tmp_path)})
    assert _seq(tmp_path, "n") == [("start", 1), ("error", 1), ("start", 2), ("end", 2)]
    assert _events(tmp_path)[1]["error"] == "ConnectionError: blip"


def test_persistent_error_reraised_unchanged(tmp_path):
    def node_bad(state):
        raise ValueError("x" * 1000)

    with pytest.raises(ValueError) as ei:
        _one_node_graph(node_bad, retry=_FAST_RETRY).invoke({"run_dir": str(tmp_path)})
    assert str(ei.value) == "x" * 1000
    ev = _events(tmp_path)
    assert [(e["event"], e["attempt"]) for e in ev] == [("start", 1), ("error", 1)]
    assert len(ev[1]["error"]) == 500
    assert ev[1]["error"].startswith("ValueError: xxx")


def test_interrupt_paused_then_resumed(tmp_path):
    def node_hitl(state):
        answer = interrupt({"kind": "approve"})
        return {"other": answer}

    graph = _one_node_graph(node_hitl, saver=InMemorySaver())
    config = {"configurable": {"thread_id": "t1"}}
    graph.invoke({"run_dir": str(tmp_path)}, config=config)
    assert _seq(tmp_path, "n") == [("start", 1), ("paused", 1)]
    out = graph.invoke(Command(resume="yes"), config=config)
    assert out["other"] == "yes"
    assert _seq(tmp_path, "n") == [("start", 1), ("paused", 1), ("start", 2), ("end", 2)]


def test_no_run_dir_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    def node_ok(state):
        return {"other": "v"}

    assert _one_node_graph(node_ok).invoke({})["other"] == "v"
    assert list(tmp_path.iterdir()) == []


def test_wrapper_keeps_name():
    def node_named(state):
        return {}

    assert pgraph._instrumented("n", node_named).__name__ == "node_named"


def test_build_graph_wraps_every_node(tmp_path, monkeypatch):
    monkeypatch.setattr(pgraph, "CHECKPOINT_DB", tmp_path / "cp.db")
    compiled, saver = pgraph.build_graph("transcribe")
    try:
        for name, spec in compiled.builder.nodes.items():
            assert getattr(spec.runnable.func, "__wrapped__", None) is not None, name
    finally:
        saver.conn.close()


# --- run.json ----------------------------------------------------------------


def _run_graph(node_fn, saver=None):
    """Tiny real graph on the pipeline State so run.py's initial state fits."""
    g = StateGraph(State)
    g.add_node("n", pgraph._instrumented("n", node_fn))
    g.add_edge(START, "n")
    g.add_edge("n", END)
    return g.compile(checkpointer=saver or InMemorySaver())


class _Saver:
    class conn:  # noqa: N801
        @staticmethod
        def close():
            pass


def _record(tmp_path: Path) -> Dict[str, Any]:
    return json.loads((tmp_path / "processed" / "rid" / "langgraph" / "run.json").read_text())


def _args(tmp_path: Path, **kw) -> argparse.Namespace:
    transcript = tmp_path / "transcript_2026-10-31.txt"
    transcript.write_text("hello world")
    base = dict(
        from_transcript=str(transcript), profile="sandbox", run_suffix="langgraph",
        auto_approve=False, telegram_on_error=False, reminder_interval=1, target_seconds=600,
    )
    base.update(kw)
    return argparse.Namespace(**base)


@pytest.fixture
def harness(tmp_path, monkeypatch):
    monkeypatch.setattr(run, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(run, "telegram_send", lambda msg: 0)

    def use(node_fn):
        monkeypatch.setattr(run, "build_graph", lambda start_at: (_run_graph(node_fn), _Saver))

    return use


def test_run_json_done_with_hitl(tmp_path, monkeypatch, harness):
    seen: List[str] = []

    def node(state):
        answer = interrupt({"kind": "approve", "proposal": "p"})
        seen.append(_record(tmp_path)["status"])
        return {"validator_verdict": "PASS", "summary_report_path": answer}

    def fake_ask(prompt, interval):
        seen.append(_record(tmp_path)["status"])
        return "yes"

    harness(node)
    monkeypatch.setattr(run, "_ask_indefinitely", fake_ask)
    assert run._process_one_video(_args(tmp_path), "rid") == 0
    assert seen == ["waiting_hitl", "running"]
    rec = _record(tmp_path)
    assert set(rec) == {"run_id", "status", "verdict", "pid", "profile", "input", "started_at", "ended_at", "hitl_wait_s", "error"}
    assert rec["run_id"] == "rid"
    assert rec["status"] == "done" and rec["verdict"] == "PASS" and rec["error"] is None
    assert rec["pid"] == os.getpid() and rec["profile"] == "sandbox"
    assert rec["input"] == {"kind": "transcript", "path": str((tmp_path / "transcript_2026-10-31.txt").resolve())}
    assert rec["ended_at"] >= rec["started_at"]
    assert isinstance(rec["hitl_wait_s"], float)
    assert not list((tmp_path / "processed" / "rid" / "langgraph").glob("*.tmp"))


def test_run_json_auto_approve_never_waits(tmp_path, monkeypatch, harness):
    def node(state):
        interrupt({"kind": "approve"})
        return {}

    def boom(*a, **k):
        raise AssertionError("must not ask")

    harness(node)
    monkeypatch.setattr(run, "_ask_indefinitely", boom)
    assert run._process_one_video(_args(tmp_path, auto_approve=True), "rid") == 2
    rec = _record(tmp_path)
    assert rec["status"] == "done" and rec["verdict"] is None


def test_run_json_failed_on_error(tmp_path, harness):
    def node(state):
        raise ValueError("bad " + "y" * 1000)

    harness(node)
    with pytest.raises(ValueError):
        run._process_one_video(_args(tmp_path, auto_approve=True), "rid")
    rec = _record(tmp_path)
    assert rec["status"] == "failed"
    assert rec["error"].startswith("ValueError: bad y") and len(rec["error"]) == 500
    assert rec["ended_at"] is not None


def test_run_json_waiting_reply_then_running_on_retry(tmp_path, monkeypatch, harness):
    seen: List[str] = []
    calls = {"n": 0}

    def node(state):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("flaky")
        seen.append(_record(tmp_path)["status"])
        return {"validator_verdict": "PARTIAL"}

    def fake_bounded(prompt, interval, max_reminders=5):
        seen.append(_record(tmp_path)["status"])
        return "retry"

    harness(node)
    monkeypatch.setattr(run, "_ask_bounded", fake_bounded)
    assert run._process_one_video(_args(tmp_path), "rid") == 0
    assert seen == ["waiting_reply", "running"]
    assert _record(tmp_path)["status"] == "done"


def test_run_json_failed_after_abort_reply(tmp_path, monkeypatch, harness):
    def node(state):
        raise RuntimeError("nope")

    harness(node)
    monkeypatch.setattr(run, "_ask_bounded", lambda *a, **k: "abort")
    with pytest.raises(RuntimeError):
        run._process_one_video(_args(tmp_path), "rid")
    rec = _record(tmp_path)
    assert rec["status"] == "failed" and rec["error"] == "RuntimeError: nope"


# --- run id from outside -----------------------------------------------------


@pytest.mark.parametrize("flag", ["--video", "--from-transcript"])
def test_run_id_used_verbatim(tmp_path, monkeypatch, flag):
    src = tmp_path / ("in.mp4" if flag == "--video" else "in.txt")
    src.write_text("x")
    got = {}

    def fake_process(args, run_id, *, video=None):
        got["run_id"] = run_id
        return 0

    monkeypatch.setattr(run, "_process_one_video", fake_process)
    monkeypatch.setattr(sys, "argv", ["run.py", flag, str(src), "--profile", "sandbox", "--run-id", "ui-2026-10-31_abc"])
    assert run.main() == 0
    assert got["run_id"] == "ui-2026-10-31_abc"
