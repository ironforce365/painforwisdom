"""call_llm shells out to the claude CLI and returns the .result text."""
from __future__ import annotations
import json

import pytest

import eval.llm as llm


def test_call_llm_parses_result(monkeypatch):
    captured = {}

    class FakeCompleted:
        returncode = 0
        stdout = json.dumps({"type": "result", "is_error": False, "result": "HELLO"})
        stderr = ""

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return FakeCompleted()

    monkeypatch.setattr(llm.subprocess, "run", fake_run)
    out = llm.call_llm(system="sys", user="usr", model="claude-sonnet-4-6")
    assert out == "HELLO"
    assert "claude" in captured["cmd"][0]
    assert "-p" in captured["cmd"]
    assert "--output-format" in captured["cmd"] and "json" in captured["cmd"]
    assert "claude-sonnet-4-6" in captured["cmd"]


def test_call_llm_raises_on_api_error(monkeypatch):
    class FakeCompleted:
        returncode = 0
        stdout = json.dumps(
            {"type": "result", "is_error": True, "result": "", "api_error_status": "overloaded"}
        )
        stderr = ""

    monkeypatch.setattr(llm.subprocess, "run", lambda cmd, **kw: FakeCompleted())
    with pytest.raises(llm.LLMError):
        llm.call_llm(system="s", user="u")


def test_call_llm_raises_on_nonzero_exit(monkeypatch):
    class FakeCompleted:
        returncode = 1
        stdout = ""
        stderr = "boom"

    monkeypatch.setattr(llm.subprocess, "run", lambda cmd, **kw: FakeCompleted())
    with pytest.raises(llm.LLMError):
        llm.call_llm(system="s", user="u")


class _FakeOk:
    returncode = 0
    stdout = json.dumps({"type": "result", "is_error": False, "result": "ok"})
    stderr = ""


def _capture_timeout(monkeypatch):
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["timeout"] = kwargs.get("timeout")
        return _FakeOk()

    monkeypatch.setattr(llm.subprocess, "run", fake_run)
    return captured


def test_call_llm_default_timeout_is_60s(monkeypatch):
    # 2026-07-04 outage: with the API unreachable, each gate/guard `claude -p`
    # call blocked for the full hardcoded 120s. Healthy classifier calls run
    # 5–10s; 60s is generous headroom at half the worst-case cost.
    monkeypatch.delenv("COACH_LLM_TIMEOUT_S", raising=False)
    captured = _capture_timeout(monkeypatch)
    llm.call_llm(system="s", user="u")
    assert captured["timeout"] == 60.0


def test_call_llm_timeout_env_override(monkeypatch):
    monkeypatch.setenv("COACH_LLM_TIMEOUT_S", "25")
    captured = _capture_timeout(monkeypatch)
    llm.call_llm(system="s", user="u")
    assert captured["timeout"] == 25.0
