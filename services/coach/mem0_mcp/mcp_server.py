"""FastMCP stdio server exposing Mem0 as MCP tools."""
from __future__ import annotations
import os
from fastmcp import FastMCP

from mem0_mcp.client import Mem0Client
from shared.validation import validate_telegram_user_id

mcp = FastMCP("coach-mem0")
_client = Mem0Client(base_url=os.environ.get("MEM0_API_URL", "http://mem0-api:8000"))


@mcp.tool()
def mem0_add(user_id: str, text: str) -> dict:
    """Store a fact extracted from the turn. Call AFTER each user message."""
    validate_telegram_user_id(user_id)
    return _client.add(user_id, text)


@mcp.tool()
def mem0_search(user_id: str, query: str, limit: int = 5) -> list[dict]:
    """Recall up to `limit` facts about user_id relevant to query. Call BEFORE replying."""
    validate_telegram_user_id(user_id)
    return _client.search(user_id, query, limit)


if __name__ == "__main__":
    mcp.run(transport="stdio")
