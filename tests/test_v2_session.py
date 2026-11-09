"""pipeline.v2.session and the call_llm backend hook (spec 2026-11-09-session-pipeline-v2 §2)."""
import json

import claude_agent_sdk as sdk
import pytest

from pipeline import llm, usage
from pipeline.v2 import session

KEYS = {"text", "duration_s", "input_tokens", "output_tokens", "cache_read_tokens", "cache_creation_tokens",
        "cost_usd", "billing_mode", "model", "stop_reason", "attempts"}


def test_call_llm_uses_bound_backend_and_unbinds(monkeypatch):
    seen = {}

    def fn(**kw):
        seen.update(kw)
        return {"text": "hi"}

    def boom(**kw):
        raise AssertionError("litellm path must not run")

    monkeypatch.setattr(llm.litellm, "completion", boom)
    with llm.backend(fn):
        assert llm.call_llm("m", "sys", "user", max_tokens=7, web_search=True) == {"text": "hi"}
    assert seen == {"model": "m", "system_prompt": "sys", "user_message": "user", "max_tokens": 7,
                    "web_search": True, "tools": None}
    assert llm._BACKEND.get() is None


def test_backend_maps_tools_turns_cwd_and_reports_dict(tmp_path, monkeypatch, caplog):
    fake = session.FakeSession("plain", session.SessionResult(text="long", session_id="s2", model="m",
                                                              output_tokens=50, attempts=3))
    monkeypatch.setattr(session, "run_session", fake)
    audit = []
    backend = session.session_backend(lambda r, req: audit.append((r, req)), cwd=str(tmp_path))

    out = backend(model="m", system_prompt="S", user_message="U", max_tokens=100)
    assert set(out) == KEYS
    assert (out["text"], out["cost_usd"], out["billing_mode"]) == ("plain", 0.0, "subscription-session")
    assert fake.calls[0]["allowed_tools"] == () and fake.calls[0]["max_turns"] == 1
    assert fake.calls[0]["cwd"] == str(tmp_path)

    out = backend(model="m", system_prompt="S", user_message="U", max_tokens=10, web_search=True)
    assert fake.calls[1]["allowed_tools"] == ("WebSearch", "WebFetch") and fake.calls[1]["max_turns"] == 30
    assert out["text"] == "long" and out["attempts"] == 3  # over max_tokens: kept whole, warned
    assert "max_tokens 10" in caplog.text
    assert audit[1][1] == {"system_prompt": "S", "user_message": "U", "model": "m",
                           "tools": ["WebSearch", "WebFetch"], "max_tokens": 10}


def test_on_session_failure_never_fails_the_call(tmp_path, monkeypatch):
    monkeypatch.setattr(session, "run_session", session.FakeSession("ok"))

    def broken(r, req):
        raise OSError("disk full")

    assert session.session_backend(broken, cwd=str(tmp_path))(
        model="m", system_prompt="S", user_message="U", max_tokens=10)["text"] == "ok"


def _result(**kw):
    base = dict(subtype="success", duration_ms=1, duration_api_ms=1, is_error=False, num_turns=1,
                session_id="sid", stop_reason="end_turn", total_cost_usd=0.02, result=" answer ",
                usage={"input_tokens": 3, "output_tokens": 4, "cache_read_input_tokens": 5,
                       "cache_creation_input_tokens": 6})
    base.update(kw)
    return sdk.ResultMessage(**base)


def test_run_session_isolation_usage_and_retry(tmp_path, monkeypatch):
    calls, sleeps = [], []
    script = [sdk.CLIConnectionError("dropped"),
              [sdk.SystemMessage(subtype="init", data={"claude_code_version": "9.9"}), _result()]]

    async def fake_query(*, prompt, options):
        calls.append(options)
        item = script.pop(0)
        if isinstance(item, Exception):
            raise item
        for m in item:
            yield m

    monkeypatch.setattr(session.sdk, "query", fake_query)
    monkeypatch.setattr(session, "_sleep", sleeps.append)
    with usage.bind(str(tmp_path), "extractor", 1):
        res = session.run_session("S", "U", model="m", max_turns=1, cwd=str(tmp_path))

    opts = calls[-1]
    assert opts.setting_sources == [] and opts.tools == [] and opts.allowed_tools == []
    assert opts.env == {"ANTHROPIC_API_KEY": ""} and opts.cwd == str(tmp_path) and opts.system_prompt == "S"
    assert sleeps == [4.0] and res.attempts == 2
    assert (res.text, res.cli_version, res.notional_cost_usd) == ("answer", "9.9", 0.02)
    assert res.usage() == {"input_tokens": 3, "output_tokens": 4, "cache_read_tokens": 5,
                           "cache_creation_tokens": 6}
    json.dumps(res.messages)
    events = [json.loads(line)["event"] for line in (tmp_path / "usage.jsonl").read_text().splitlines()]
    assert events == ["start", "error", "start", "end"]


def test_run_session_raises_non_transient_without_retry(tmp_path, monkeypatch):
    async def fake_query(*, prompt, options):
        yield _result(is_error=True, subtype="error_max_turns", result=None)

    monkeypatch.setattr(session.sdk, "query", fake_query)
    monkeypatch.setattr(session, "_sleep", lambda s: pytest.fail("must not retry"))
    with pytest.raises(session.SessionError, match="error_max_turns"):
        session.run_session("S", "U", model="m", max_turns=1, cwd=str(tmp_path))
