"""FastAPI service: POST /turn (user_id, text) → coach reply.

Wires together:
- crisis pre-filter
- claude-agent-sdk client with 3 MCP servers (vault_rag, user_memory, mem0)
- post-turn inbox hook
"""
from __future__ import annotations
import os
from pathlib import Path
from typing import Tuple

from fastapi import FastAPI
from pydantic import BaseModel

from agent.hooks import write_inbox_entry
from agent.session_map import SessionMap
from crisis.filter import check_message

app = FastAPI(title="painforwisdom-coach")
_sessions = SessionMap()
_inbox_root = Path(os.environ.get("COACH_INBOX_ROOT", "/vault/_inbox"))


class Turn(BaseModel):
    user_id: str
    text: str


class Reply(BaseModel):
    reply: str
    crisis: bool


async def _chat_with_agent(user_id: str, text: str) -> Tuple[str, list[str]]:
    """Real implementation: spin up ClaudeSDKClient with MCP servers, call query,
    collect assistant text + retrieved source slugs. Monkeypatched in tests."""
    from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions
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
    sources: list[str] = []
    reply_chunks: list[str] = []
    async with ClaudeSDKClient(options=options) as client:
        await client.query(f"<user_id>{user_id}</user_id>\n\n{text}")
        async for msg in client.receive_response():
            if hasattr(msg, "content"):
                for block in msg.content:
                    if getattr(block, "type", None) == "text":
                        reply_chunks.append(block.text)
                    elif getattr(block, "type", None) == "tool_use" and block.name.endswith("search_vault"):
                        sources.append(str(block.input.get("query", "")))
    return "".join(reply_chunks), sources


@app.post("/turn", response_model=Reply)
async def turn(t: Turn) -> Reply:
    hit = check_message(t.text)
    if hit is not None:
        return Reply(reply=hit.canned_reply, crisis=True)

    reply_text, sources = await _chat_with_agent(t.user_id, t.text)
    inbox_root = Path(os.environ.get("COACH_INBOX_ROOT", str(_inbox_root)))
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
