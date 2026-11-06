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
import threading
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
    assert set(end) == {"ts", "node", "event", "attempt", "duration_s", "noop", "outputs", "result"}
    assert end["result"] == {"other": "v"}


def test_end_result_keeps_scalars_and_audit_keys_only():
    big = {"detail": "y" * 600}
    result = pgraph.end_result({
        "out_path": "/x/o.md", "s": "x" * 400, "n": 3, "f": 1.5, "b": True, "none": None, "blob": {"a": 1}, "many": [1],
        "pre_findings": [{"ok": True}] * 60, "branch_findings_research": [big], "branch_verdict_x": {"v": 1},
    })
    assert result["s"] == "x" * 300
    assert {k: result[k] for k in ("n", "f", "b", "none")} == {"n": 3, "f": 1.5, "b": True, "none": None}
    assert not {"out_path", "blob", "many"} & set(result)
    assert len(result["pre_findings"]) == 50 and result["pre_findings"][0] == '{"ok": true}'
    assert len(result["branch_findings_research"][0]) == 500
    assert result["branch_verdict_x"] == ['{"v": 1}']
    assert pgraph.end_result(None) == {}


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
        from_transcript=str(transcript), language=None, profile="sandbox", run_suffix="langgraph",
        auto_approve=False, telegram_on_error=False, reminder_interval=1, target_seconds=600,
        processed_root=tmp_path / "processed",
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

    def fake_ask(rd, request, interval, max_reminders=None):
        assert request["kind"] == "approval" and request["node"] == "n" and max_reminders is None
        seen.append(_record(tmp_path)["status"])
        return "yes"

    harness(node)
    monkeypatch.setattr(run.hitl, "ask", fake_ask)
    assert run._process_one_video(_args(tmp_path), "rid") == 0
    assert seen == ["waiting_hitl", "running"]
    rec = _record(tmp_path)
    assert set(rec) == {"run_id", "status", "verdict", "pid", "profile", "language", "input", "started_at", "ended_at", "hitl_wait_s", "error"}
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
    monkeypatch.setattr(run.hitl, "ask", boom)
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

    def fake_bounded(rd, request, interval, max_reminders=None):
        assert request["kind"] == "error" and "flaky" in request["error"] and max_reminders == 5
        seen.append(_record(tmp_path)["status"])
        return "retry"

    harness(node)
    monkeypatch.setattr(run.hitl, "ask", fake_bounded)
    assert run._process_one_video(_args(tmp_path), "rid") == 0
    assert seen == ["waiting_reply", "running"]
    assert _record(tmp_path)["status"] == "done"


def test_run_json_failed_after_abort_reply(tmp_path, monkeypatch, harness):
    def node(state):
        raise RuntimeError("nope")

    harness(node)
    monkeypatch.setattr(run.hitl, "ask", lambda *a, **k: "abort")
    with pytest.raises(RuntimeError):
        run._process_one_video(_args(tmp_path), "rid")
    rec = _record(tmp_path)
    assert rec["status"] == "failed" and rec["error"] == "RuntimeError: nope"


def test_second_failure_re_enters_the_prompt_loop(tmp_path, monkeypatch, harness):  # [DT-E2]
    calls = {"n": 0}
    asks: List[str] = []

    def node(state):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise RuntimeError(f"flaky {calls['n']}")
        return {"validator_verdict": "PASS"}

    def fake_ask(rd, request, interval, max_reminders=None):
        asks.append(request["error"])
        return "retry"

    harness(node)
    monkeypatch.setattr(run.hitl, "ask", fake_ask)
    assert run._process_one_video(_args(tmp_path), "rid") == 0
    assert asks == ["RuntimeError: flaky 1", "RuntimeError: flaky 2"]
    assert _record(tmp_path)["status"] == "done"


def test_budget_exhausted_fails_the_run(tmp_path, monkeypatch, harness):  # [DT-E5]
    def node(state):
        raise RuntimeError("nope")

    harness(node)
    monkeypatch.setattr(run.hitl, "ask", lambda *a, **k: None)
    with pytest.raises(RuntimeError):
        run._process_one_video(_args(tmp_path), "rid")
    assert _record(tmp_path)["status"] == "failed"


def test_non_recoverable_offers_only_abort(tmp_path, monkeypatch, harness):  # [DT-E4]
    seen: List[Dict[str, Any]] = []

    def node(state):
        raise RuntimeError("Invalid authentication credentials")

    def fake_ask(rd, request, interval, max_reminders=None):
        seen.append(request)
        return "abort"

    harness(node)
    monkeypatch.setattr(run.hitl, "ask", fake_ask)
    with pytest.raises(RuntimeError):
        run._process_one_video(_args(tmp_path), "rid")
    assert [o["reply"] for o in seen[0]["options"]] == ["abort"] and seen[0]["non_recoverable"]


def test_auto_approve_refuses_a_curator_decision(tmp_path, monkeypatch, harness):  # [DT-A5]
    def node(state):
        interrupt({"kind": "curator", "proposal": "ALREADY_PROCESSED"})
        return {}

    harness(node)
    with pytest.raises(RuntimeError, match="ALREADY_PROCESSED"):
        run._process_one_video(_args(tmp_path, auto_approve=True), "rid")


def test_real_mailbox_answers_a_failing_run(tmp_path, monkeypatch, harness):  # [DT-E1]
    calls = {"n": 0}

    def node(state):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("flaky")
        return {"validator_verdict": "PASS"}

    real_ask = run.hitl.ask

    def _answer(rd):
        assert run.hitl.write_reply(rd, run.hitl.read_pending(rd)["id"], "retry")

    def answering(rd, request, interval, max_reminders=None):
        threading.Timer(0.1, _answer, (rd,)).start()
        return real_ask(rd, request, 30, max_reminders, telegram=False, poll_s=0.01)

    harness(node)
    monkeypatch.setattr(run.hitl, "ask", answering)
    assert run._process_one_video(_args(tmp_path), "rid") == 0
    rd = tmp_path / "processed" / "rid" / "langgraph"
    assert not (rd / "pending.json").exists() and not (rd / "reply.json").exists()


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


# --- processed root from outside -------------------------------------------


def test_processed_root_flag_default_and_override(tmp_path, monkeypatch):
    src = tmp_path / "in.txt"
    src.write_text("x")
    got = []
    monkeypatch.setattr(run, "_process_one_video", lambda args, run_id, *, video=None: got.append(args.processed_root) or 0)
    monkeypatch.setattr(sys, "argv", ["run.py", "--from-transcript", str(src), "--profile", "sandbox"])
    run.main()
    monkeypatch.setattr(sys, "argv", ["run.py", "--from-transcript", str(src), "--profile", "sandbox",
                                      "--processed-root", str(tmp_path / "elsewhere")])
    run.main()
    assert got == [run.PROJECT_ROOT / "processed", tmp_path / "elsewhere"]


def test_run_dir_under_processed_root(tmp_path, harness):
    harness(lambda state: {"validator_verdict": "PASS"})
    root = tmp_path / "custom"
    assert run._process_one_video(_args(tmp_path, processed_root=root), "rid") == 0
    assert json.loads((root / "rid" / "langgraph" / "run.json").read_text())["status"] == "done"
    assert not (tmp_path / "processed").exists()


def test_batch_archives_under_processed_root(tmp_path, monkeypatch):
    videos = tmp_path / "videos"
    videos.mkdir()
    (videos / "a.mp4").write_text("x")
    monkeypatch.setattr(run, "_process_one_video", lambda args, run_id, *, video=None: 0)
    monkeypatch.setattr(run, "telegram_send", lambda msg: 0)
    root = tmp_path / "custom"
    args = argparse.Namespace(dir=str(videos), run_id="b", processed_root=root)
    assert run._run_batch(args) == 0
    assert (root / "b_001" / "source" / "a.mp4").is_file()


def test_the_chosen_language_enters_the_graph_state(tmp_path):
    args = argparse.Namespace(from_transcript=None, language="es", profile="prod")
    initial, _ = run._build_initial_state(args, Path("v.mp4"), "r", tmp_path)
    assert initial["language"] == "es"


def test_without_a_choice_the_run_takes_the_catalog_language(tmp_path):
    transcript = tmp_path / "t.txt"
    transcript.write_text("words")
    args = argparse.Namespace(from_transcript=str(transcript), language=None, profile="prod")
    initial, start = run._build_initial_state(args, None, "r", tmp_path)
    assert initial["language"] == "en" and start == "extract"


def test_the_graph_keeps_the_language_in_its_state():
    assert "language" in State.__annotations__
