"""FastAPI service: POST /turn (user_id, text) → coach reply.

Wires together:
- crisis pre-filter
- claude-agent-sdk client with 3 MCP servers (vault_rag, user_memory, mem0)
- post-turn inbox hook
"""
from __future__ import annotations
import json
import os
from pathlib import Path
from typing import AsyncIterator, Tuple

from claude_agent_sdk import TextBlock, ToolResultBlock, ToolUseBlock
from fastapi import FastAPI
from pydantic import BaseModel

from agent.hooks import write_inbox_entry
from agent.session_map import SessionMap
from crisis.filter import check_message

# Namespaced MCP tool name. The claude-agent-sdk MCP tool naming convention is
# `mcp__<server_key>__<tool>` where `<server_key>` is the key in the
# `mcp_servers` dict passed to ClaudeAgentOptions (NOT the FastMCP constructor
# name).
SEARCH_VAULT_TOOL = "mcp__vault_rag__search_vault"

app = FastAPI(title="painforwisdom-coach")
_sessions = SessionMap()


class Turn(BaseModel):
    user_id: str
    text: str


class Reply(BaseModel):
    reply: str
    crisis: bool


def _extract_source_slugs(content) -> list[str]:
    """Pull source slugs from a search_vault tool result.

    The SDK's ToolResultBlock.content can take several shapes depending on the
    FastMCP version that produced it:

    - A list of dicts (FastMCP structured return), e.g.
      `[{"text": ..., "source": <slug>, "score": ...}, ...]`
    - A list wrapping the structured payload as text, e.g.
      `[{"type": "text", "text": "<json>"}]`
    - A bare JSON-encoded string.
    """
    if isinstance(content, str):
        try:
            content = json.loads(content)
        except (json.JSONDecodeError, ValueError):
            return []
    if not isinstance(content, list):
        return []
    slugs: list[str] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        if "source" in item:
            slugs.append(str(item["source"]))
            continue
        if item.get("type") == "text":
            try:
                inner = json.loads(item.get("text", ""))
            except (json.JSONDecodeError, ValueError):
                continue
            if isinstance(inner, list):
                for sub in inner:
                    if isinstance(sub, dict) and "source" in sub:
                        slugs.append(str(sub["source"]))
    return slugs


async def _extract_reply_and_sources(messages: AsyncIterator) -> Tuple[str, list[str]]:
    """Walk an async iterator of SDK messages, concatenating assistant text
    and collecting source slugs from search_vault tool results.

    Pulled out of `_chat_with_agent` so the parsing logic can be unit-tested
    without spinning up a real ClaudeSDKClient.
    """
    sources: list[str] = []
    reply_chunks: list[str] = []
    search_vault_tool_use_ids: set[str] = set()

    async for msg in messages:
        if not hasattr(msg, "content"):
            continue
        content = msg.content
        # UserMessage.content may be a bare string; only iterable block lists
        # carry tool-use / tool-result information we care about.
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, TextBlock):
                reply_chunks.append(block.text)
            elif isinstance(block, ToolUseBlock) and block.name == SEARCH_VAULT_TOOL:
                search_vault_tool_use_ids.add(block.id)
            elif isinstance(block, ToolResultBlock) and block.tool_use_id in search_vault_tool_use_ids:
                sources.extend(_extract_source_slugs(block.content))
    return "".join(reply_chunks), sources


async def _chat_with_agent(user_id: str, text: str) -> Tuple[str, list[str]]:
    """Real implementation: spin up ClaudeSDKClient with MCP servers, call query,
    collect assistant text + retrieved source slugs. Monkeypatched in tests."""
    from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient
    from claude_agent_sdk.types import McpStdioServerConfig

    session_id = _sessions.get_or_create_session_id(user_id)
    coach_prompt = Path(__file__).parent.parent / "coach_prompt.md"
    options = ClaudeAgentOptions(
        system_prompt=coach_prompt.read_text(encoding="utf-8"),
        resume=session_id,
        mcp_servers={
            "vault_rag": McpStdioServerConfig(
                command="python", args=["-m", "vault_rag.mcp_server"], env=dict(os.environ),
            ),
            "user_memory": McpStdioServerConfig(
                command="python", args=["-m", "user_memory.mcp_server"], env=dict(os.environ),
            ),
            "mem0": McpStdioServerConfig(
                command="python", args=["-m", "mem0_mcp.mcp_server"], env=dict(os.environ),
            ),
        },
    )
    async with ClaudeSDKClient(options=options) as client:
        await client.query(f"<user_id>{user_id}</user_id>\n\n{text}")
        return await _extract_reply_and_sources(client.receive_response())


@app.post("/turn", response_model=Reply)
async def turn(t: Turn) -> Reply:
    hit = check_message(t.text)
    if hit is not None:
        return Reply(reply=hit.canned_reply, crisis=True)

    reply_text, sources = await _chat_with_agent(t.user_id, t.text)
    inbox_root = Path(os.environ.get("COACH_INBOX_ROOT", "/vault/_inbox"))
    write_inbox_entry(
        inbox_root=inbox_root,
        user_id=t.user_id,
        user_text=t.text,
        assistant_text=reply_text,
        retrieved_sources=sources,
    )
    return Reply(reply=reply_text, crisis=False)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}
