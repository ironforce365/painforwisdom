"""mem0 MCP tools validate user_id before calling backend."""
from __future__ import annotations
import pytest


def test_mem0_add_rejects_bad_user_id():
    from mem0_mcp.mcp_server import mem0_add
    with pytest.raises(ValueError):
        mem0_add(user_id="abc", text="x")


def test_mem0_search_rejects_bad_user_id():
    from mem0_mcp.mcp_server import mem0_search
    with pytest.raises(ValueError):
        mem0_search(user_id="", query="anything")
