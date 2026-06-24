"""Monitoring web service (F2 + F3).

A tiny read-only FastAPI app over the conversation log volume. It powers a
single-page UI: a list of active users (name, last-message time, last message)
and, on click, the full conversation for one user — byte-capped so a long
history can't blow up the browser.

It is strictly a reader of the JSONL the Telegram bot writes; it never calls the
coach or mutates state."""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from monitor.app import create_app
from telegram_bot.conversation_log import ConversationLog


@pytest.fixture
def client(tmp_path, monkeypatch):
    convo_dir = tmp_path / "conversations"
    monkeypatch.setenv("COACH_CONVO_LOG_DIR", str(convo_dir))
    log = ConversationLog(convo_dir)
    log.append(1, "user", "hola coach", name="Ana", ts="2026-06-21T10:00:00+00:00")
    log.append(1, "coach", "Hola Ana, ¿en qué trabajas?", ts="2026-06-21T10:00:30+00:00")
    log.append(2, "user", "hi there", name="Bob", ts="2026-06-21T12:00:00+00:00")
    return TestClient(create_app())


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200


def test_users_endpoint_lists_active_users(client):
    r = client.get("/api/users")
    assert r.status_code == 200
    users = r.json()["users"]
    # Bob is most recent → first.
    assert [u["user_id"] for u in users] == ["2", "1"]
    ana = next(u for u in users if u["user_id"] == "1")
    assert ana["name"] == "Ana"
    assert ana["last_text"] == "Hola Ana, ¿en qué trabajas?"
    assert ana["last_ts"] == "2026-06-21T10:00:30+00:00"


def test_conversation_endpoint_returns_messages(client):
    r = client.get("/api/conversations/1")
    assert r.status_code == 200
    body = r.json()
    assert body["user_id"] == "1"
    msgs = body["messages"]
    assert [m["role"] for m in msgs] == ["user", "coach"]
    assert msgs[0]["text"] == "hola coach"


def test_conversation_missing_user_is_empty(client):
    r = client.get("/api/conversations/999")
    assert r.status_code == 200
    assert r.json()["messages"] == []


def test_index_serves_html(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    # The page wires itself to the JSON API.
    assert "/api/users" in r.text


def test_conversation_is_byte_capped(client, tmp_path, monkeypatch):
    # Append a large history for a fresh user, then assert the endpoint caps it.
    log = ConversationLog(tmp_path / "conversations")
    for i in range(500):
        log.append(3, "user", f"msg {i} " + "y" * 100,
                   ts=f"2026-06-21T10:{i // 60:02d}:{i % 60:02d}+00:00")
    r = client.get("/api/conversations/3?max_bytes=5000")
    assert r.status_code == 200
    msgs = r.json()["messages"]
    # Capped: earliest dropped, latest kept, every record parseable.
    assert "msg 0 " not in msgs[0]["text"]
    assert "msg 499" in msgs[-1]["text"]
    assert all("role" in m and "text" in m for m in msgs)
