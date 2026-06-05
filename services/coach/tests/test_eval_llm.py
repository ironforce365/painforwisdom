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
