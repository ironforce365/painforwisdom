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


def test_reset_drops_session_and_user_memory(client, monkeypatch):
    # /restart calls this so the coach truly starts over for one user.
    calls = {}
    monkeypatch.setattr(svc._sessions, "reset", lambda uid: calls.__setitem__("session", uid))
    monkeypatch.setattr(svc, "delete_user_memory", lambda uid: calls.__setitem__("mem", uid))

    r = client.post("/reset", json={"user_id": "42"})
    assert r.status_code == 200
    assert r.json()["status"] == "reset"
    assert calls == {"session": "42", "mem": "42"}


@pytest.mark.asyncio
async def test_message_parsing_extracts_text_and_source_slugs():
    """_extract_reply_and_sources concatenates assistant text and surfaces
    source slugs from search_vault tool results — correlating ToolResultBlock
    back to its originating ToolUseBlock by id."""
    from claude_agent_sdk import (
        AssistantMessage,
        TextBlock,
        ToolResultBlock,
        ToolUseBlock,
        UserMessage,
    )

    from agent.service import _extract_reply_and_sources

    tool_id = "toolu_01"
    other_tool_id = "toolu_02"
    messages = [
        # Assistant invokes search_vault and an unrelated tool in parallel.
        AssistantMessage(
            content=[
                ToolUseBlock(
                    id=tool_id,
                    name="mcp__vault_rag__search_vault",
                    input={"query": "rain"},
                ),
                ToolUseBlock(
                    id=other_tool_id,
                    name="mcp__user_memory__read_user_memory",
                    input={"user_id": "1"},
                ),
            ],
            model="claude-sonnet-4-6",
        ),
        # Tool results come back as a UserMessage with a list of result blocks.
        UserMessage(
            content=[
                ToolResultBlock(
                    tool_use_id=tool_id,
                    content=[
                        {"source": "comfort-as-default", "text": "...", "score": 0.9},
                        {"source": "deliberate-discomfort", "text": "...", "score": 0.8},
                    ],
                    is_error=False,
                ),
                # Result from the unrelated tool — its slugs must NOT be picked up.
                ToolResultBlock(
                    tool_use_id=other_tool_id,
                    content=[{"source": "should-be-ignored", "text": "..."}],
                    is_error=False,
                ),
            ],
        ),
        # Assistant streams the reply across two TextBlocks.
        AssistantMessage(
            content=[TextBlock(text="Run in the rain. ")],
            model="claude-sonnet-4-6",
        ),
        AssistantMessage(
            content=[TextBlock(text="Lean into it.")],
            model="claude-sonnet-4-6",
        ),
    ]

    async def aiter():
        for m in messages:
            yield m

    reply, sources = await _extract_reply_and_sources(aiter())
    assert reply == "Run in the rain. Lean into it."
    assert sources == ["comfort-as-default", "deliberate-discomfort"]


@pytest.mark.asyncio
async def test_message_parsing_handles_json_encoded_tool_result():
    """Tool result content can also arrive as a JSON-encoded string or wrapped
    as [{"type":"text","text":"<json>"}] depending on FastMCP version — both
    shapes should yield slugs."""
    from claude_agent_sdk import (
        AssistantMessage,
        ToolResultBlock,
        ToolUseBlock,
        UserMessage,
    )

    from agent.service import _extract_reply_and_sources

    tool_id_a = "toolu_a"
    tool_id_b = "toolu_b"
    messages = [
        AssistantMessage(
            content=[
                ToolUseBlock(
                    id=tool_id_a,
                    name="mcp__vault_rag__search_vault",
                    input={"query": "rain"},
                ),
                ToolUseBlock(
                    id=tool_id_b,
                    name="mcp__vault_rag__search_vault",
                    input={"query": "fear"},
                ),
            ],
            model="claude-sonnet-4-6",
        ),
        UserMessage(
            content=[
                ToolResultBlock(
                    tool_use_id=tool_id_a,
                    content='[{"source": "slug-a", "text": "..."}]',
                    is_error=False,
                ),
                ToolResultBlock(
                    tool_use_id=tool_id_b,
                    content=[
                        {
                            "type": "text",
                            "text": '[{"source": "slug-b", "text": "..."}]',
                        }
                    ],
                    is_error=False,
                ),
            ],
        ),
    ]

    async def aiter():
        for m in messages:
            yield m

    reply, sources = await _extract_reply_and_sources(aiter())
    assert reply == ""
    assert sources == ["slug-a", "slug-b"]
