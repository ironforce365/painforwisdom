"""HTTP /turn endpoint: crisis hits short-circuit; happy path returns assistant text."""
from __future__ import annotations
import pytest
from fastapi.testclient import TestClient

import agent.service as svc


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("COACH_INBOX_ROOT", str(tmp_path))
    async def fake_chat(user_id: str, text: str):
        return ("ack: " + text, ["comfort-as-default"])
    monkeypatch.setattr(svc, "_chat_with_agent", fake_chat)
    return TestClient(svc.app)


def test_crisis_message_returns_canned(client):
    r = client.post("/turn", json={"user_id": "1", "text": "I want to die"})
    assert r.status_code == 200
    assert "988" in r.json()["reply"]
    assert r.json()["crisis"] is True


def test_happy_path(client):
    r = client.post("/turn", json={"user_id": "1", "text": "hello coach"})
    assert r.status_code == 200
    assert r.json()["reply"].startswith("ack: ")
    assert r.json()["crisis"] is False
