"""C8 usage.jsonl: pipeline.usage, its call_llm hook and the _instrumented binding. No network."""
from __future__ import annotations

import json
import os
import tempfile
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest

# pipeline.graph reads CHECKPOINT_DB_PATH at import time.
os.environ.setdefault("CHECKPOINT_DB_PATH", str(Path(tempfile.mkdtemp()) / "checkpoints.db"))

from pipeline import graph as pgraph  # noqa: E402
from pipeline import llm, usage  # noqa: E402

USAGE = {"input_tokens": 5, "output_tokens": 2, "cache_read_tokens": 1, "cache_creation_tokens": 0}


def _lines(run_dir: Path) -> List[Dict[str, Any]]:
    return [json.loads(line) for line in (run_dir / "usage.jsonl").read_text().splitlines()]


def _response() -> SimpleNamespace:
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="hi"), finish_reason="stop")],
        usage={"prompt_tokens": 5, "completion_tokens": 2, "cache_read_input_tokens": 1},
    )


@pytest.fixture(autouse=True)
def _auth(monkeypatch):
    monkeypatch.setattr(llm, "_refresh_auth", lambda: "subscription")


def test_unbound_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    call_id = usage.record_start("m")
    usage.record_end(call_id, USAGE, 0.0, 1.0)
    usage.record_error(call_id, RuntimeError("x"))
    assert list(tmp_path.iterdir()) == []


def test_line_shapes(tmp_path):
    with usage.bind(str(tmp_path), "extract", 2):
        a = usage.record_start("m")
        usage.record_end(a, USAGE, 0.25, 1.5)
        b = usage.record_start("m")
        usage.record_error(b, RuntimeError("boom"))
    assert a != b
    start, end, _, error = _lines(tmp_path)
    assert set(start) == {"ts", "event", "call_id", "node", "attempt", "model"}
    assert (start["node"], start["attempt"], start["model"]) == ("extract", 2, "m")
    assert end["model"] == "m" and end["cost_usd"] == 0.25 and end["call_s"] == 1.5 and end["input_tokens"] == 5
    assert error["model"] == "m" and error["error"] == "RuntimeError: boom"
    assert usage._MODELS == {}


def test_bind_is_per_thread_and_lines_stay_whole(tmp_path):
    def work(node: str) -> None:
        with usage.bind(str(tmp_path), node, 1):
            for _ in range(50):
                usage.record_end(usage.record_start("m"), USAGE, 0.0, 0.1)

    threads = [threading.Thread(target=work, args=(f"n{i}",)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    lines = _lines(tmp_path)  # json.loads raises on a torn line
    assert len(lines) == 8 * 100
    assert {ln["node"] for ln in lines} == {f"n{i}" for i in range(8)}
    assert usage.record_start("m") == ""  # main thread never bound


def test_write_failure_does_not_raise(tmp_path):
    with usage.bind(str(tmp_path / "missing"), "extract", 1):
        usage.record_end(usage.record_start("m"), USAGE, 0.0, 0.1)


def test_call_llm_records_each_attempt(tmp_path, monkeypatch):
    results = [Exception("429 rate_limit"), _response()]

    def fake_completion(**_):
        out = results.pop(0)
        if isinstance(out, Exception):
            raise out
        return out

    monkeypatch.setattr(llm.litellm, "completion", fake_completion)
    monkeypatch.setattr(llm.time, "sleep", lambda _: None)
    with usage.bind(str(tmp_path), "writer", 1):
        assert llm.call_llm("m", "sys", "user")["attempts"] == 2
    lines = _lines(tmp_path)
    assert [ln["event"] for ln in lines] == ["start", "error", "start", "end"]
    assert lines[0]["call_id"] == lines[1]["call_id"] != lines[2]["call_id"] == lines[3]["call_id"]
    assert lines[3]["input_tokens"] == 5 and lines[3]["cache_read_tokens"] == 1 and lines[3]["call_s"] >= 0


def test_call_llm_final_failure_records_error_and_raises(tmp_path, monkeypatch):
    def fail(**_):
        raise ValueError("bad request")

    monkeypatch.setattr(llm.litellm, "completion", fail)
    with usage.bind(str(tmp_path), "writer", 1), pytest.raises(ValueError):
        llm.call_llm("m", "sys", "user")
    assert [ln["event"] for ln in _lines(tmp_path)] == ["start", "error"]


def test_instrumented_binds_node_and_attempt_and_resets(tmp_path):
    def node(_state):
        usage.record_end(usage.record_start("m"), USAGE, 0.0, 0.1)
        return {}

    def failing(_state):
        usage.record_start("m")
        raise RuntimeError("x")

    run_dir = str(tmp_path)
    pgraph._instrumented("usage_ok", node)({"run_dir": run_dir})
    assert usage._BOUND.get() is None
    with pytest.raises(RuntimeError):
        pgraph._instrumented("usage_bad", failing)({"run_dir": run_dir})
    assert usage._BOUND.get() is None
    lines = _lines(tmp_path)
    assert [(ln["node"], ln["attempt"], ln["event"]) for ln in lines] == [
        ("usage_ok", 1, "start"), ("usage_ok", 1, "end"), ("usage_bad", 1, "start")]
    assert (tmp_path / "events.jsonl").is_file()
