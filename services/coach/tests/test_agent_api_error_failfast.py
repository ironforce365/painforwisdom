"""Fail-fast on SDK API-error replies (2026-07-04 outage).

When the Claude CLI cannot reach the API it emits the literal text
"API Error: Unable to connect to API (ConnectionRefused)" as the assistant
reply. The old pipeline treated that as a normal draft: it flowed into the
grounding gate + topic guard (two more doomed 120s `claude -p` calls), into the
vault inbox, and (when under the bot's read deadline) to the user. One dead-API
turn cost ~5 minutes and produced garbage.

New contract: an API-error draft short-circuits the turn — no gate, no guard,
no memory write, no inbox entry — and the user gets an honest, friendly retry
message immediately. A hard stream budget (COACH_STREAM_BUDGET_S) additionally
caps generation so the agent always answers before the bot's read timeout.
"""
from __future__ import annotations
import asyncio
import json

import pytest
from fastapi.testclient import TestClient

import agent.service as svc
from agent.failures import CONN_TROUBLE_REPLY, is_api_error_reply

_API_ERROR_TEXT = "API Error: Unable to connect to API (ConnectionRefused)"


# ---------------------------------------------------------------- detection

def test_detects_connection_refused_error_text():
    assert is_api_error_reply(_API_ERROR_TEXT)


def test_detects_other_api_error_prefixes():
    assert is_api_error_reply("API Error: 529 overloaded_error")
    assert is_api_error_reply("  API Error: Request timed out.")


def test_normal_coaching_reply_is_not_an_error():
    assert not is_api_error_reply("Great job showing up today — what felt hard?")
    # Mentioning an API mid-reply must not trip the detector; only a reply that
    # IS the error (starts with it) counts.
    assert not is_api_error_reply("Yesterday you said the API Error at work stressed you.")


def test_empty_reply_is_not_an_error():
    assert not is_api_error_reply("")
    assert not is_api_error_reply("   ")


# ------------------------------------------------------------ stream endpoint

def _parse_ndjson(body: str) -> list[dict]:
    return [json.loads(line) for line in body.splitlines() if line.strip()]


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("COACH_INBOX_ROOT", str(tmp_path))
    monkeypatch.setenv("COACH_DEBUG", "false")
    return TestClient(svc.app)


def test_stream_api_error_replaced_with_friendly_reply(client, monkeypatch):
    async def fake_stream(user_id: str, text: str):
        yield _API_ERROR_TEXT

    inbox_written = {}

    def fake_write_inbox(**kw):
        inbox_written.update(kw)

    async def gate_must_not_run(*a, **kw):
        raise AssertionError("gate/guard must not run on an API-error draft")

    monkeypatch.setattr(svc, "_stream_agent", fake_stream)
    monkeypatch.setattr(svc, "write_inbox_entry", fake_write_inbox)
    monkeypatch.setattr(svc, "_gate_and_guard", gate_must_not_run)

    with client.stream("POST", "/turn/stream", json={"user_id": "1", "text": "hi"}) as r:
        assert r.status_code == 200
        body = "".join(r.iter_text())

    lines = _parse_ndjson(body)
    deltas = [ln["delta"] for ln in lines if "delta" in ln]
    joined = "".join(deltas)
    assert "API Error" not in joined  # raw error text never reaches the user
    assert joined == CONN_TROUBLE_REPLY
    assert lines[-1]["done"] is True
    assert lines[-1].get("error") == "api_unreachable"
    assert inbox_written == {}  # error turns don't pollute the vault inbox


def test_stream_api_error_spanish_user_gets_spanish_reply(client, monkeypatch):
    async def fake_stream(user_id: str, text: str):
        yield _API_ERROR_TEXT

    monkeypatch.setattr(svc, "_stream_agent", fake_stream)

    with client.stream(
        "POST", "/turn/stream",
        json={"user_id": "1", "text": "hola", "language_code": "es"},
    ) as r:
        body = "".join(r.iter_text())

    deltas = [ln["delta"] for ln in _parse_ndjson(body) if "delta" in ln]
    assert "conexión" in "".join(deltas)


def test_stream_budget_exceeded_yields_friendly_reply(client, monkeypatch):
    monkeypatch.setenv("COACH_STREAM_BUDGET_S", "0.2")

    async def slow_stream(user_id: str, text: str):
        await asyncio.sleep(5)
        yield "too late"

    inbox_written = {}
    monkeypatch.setattr(svc, "_stream_agent", slow_stream)
    monkeypatch.setattr(svc, "write_inbox_entry", lambda **kw: inbox_written.update(kw))

    with client.stream("POST", "/turn/stream", json={"user_id": "1", "text": "hi"}) as r:
        body = "".join(r.iter_text())

    lines = _parse_ndjson(body)
    deltas = [ln["delta"] for ln in lines if "delta" in ln]
    assert "".join(deltas) == CONN_TROUBLE_REPLY
    assert lines[-1]["done"] is True
    assert lines[-1].get("error") == "budget_exceeded"
    assert inbox_written == {}


def test_stream_healthy_turn_unchanged(client, monkeypatch):
    async def fake_stream(user_id: str, text: str):
        for chunk in ["all ", "good"]:
            yield chunk

    written = {}
    monkeypatch.setattr(svc, "_stream_agent", fake_stream)
    monkeypatch.setattr(svc, "write_inbox_entry", lambda **kw: written.update(kw))

    with client.stream("POST", "/turn/stream", json={"user_id": "1", "text": "hi"}) as r:
        body = "".join(r.iter_text())

    lines = _parse_ndjson(body)
    deltas = [ln["delta"] for ln in lines if "delta" in ln]
    assert "".join(deltas) == "all good"
    assert lines[-1] == {"done": True, "crisis": False}
    assert written["assistant_text"] == "all good"


# ----------------------------------------------------------- blocking /turn

def test_blocking_turn_api_error_replaced_and_not_persisted(client, monkeypatch):
    async def fake_chat(user_id: str, text: str):
        return _API_ERROR_TEXT, []

    inbox_written = {}

    async def gate_must_not_run(*a, **kw):
        raise AssertionError("gate/guard must not run on an API-error draft")

    monkeypatch.setattr(svc, "_chat_with_agent", fake_chat)
    monkeypatch.setattr(svc, "write_inbox_entry", lambda **kw: inbox_written.update(kw))
    monkeypatch.setattr(svc, "_gate_and_guard", gate_must_not_run)

    r = client.post("/turn", json={"user_id": "1", "text": "hi"})
    assert r.status_code == 200
    assert r.json()["reply"] == CONN_TROUBLE_REPLY
    assert r.json()["crisis"] is False
    assert inbox_written == {}


def test_blocking_turn_budget_exceeded(client, monkeypatch):
    monkeypatch.setenv("COACH_STREAM_BUDGET_S", "0.2")

    async def slow_chat(user_id: str, text: str):
        await asyncio.sleep(5)
        return "too late", []

    monkeypatch.setattr(svc, "_chat_with_agent", slow_chat)

    r = client.post("/turn", json={"user_id": "1", "text": "hi"})
    assert r.status_code == 200
    assert r.json()["reply"] == CONN_TROUBLE_REPLY
