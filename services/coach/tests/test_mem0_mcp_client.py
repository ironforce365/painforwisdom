"""Mem0 client: add_memory + search round-trip against stubbed HTTP."""
from __future__ import annotations
import respx
from httpx import Response

from mem0_mcp.client import Mem0Client


@respx.mock
def test_add_memory_posts_to_correct_endpoint():
    route = respx.post("http://mem0-api:8000/memories").mock(
        return_value=Response(200, json={"id": "m_1"})
    )
    client = Mem0Client(base_url="http://mem0-api:8000")
    result = client.add(user_id="42", text="user prefers morning runs")
    assert result == {"id": "m_1"}
    assert route.called
    body = route.calls.last.request.content
    assert b"morning runs" in body
    assert b'"user_id":"42"' in body


@respx.mock
def test_search_returns_results():
    respx.post("http://mem0-api:8000/memories/search").mock(
        return_value=Response(200, json={"results": [{"memory": "loves cold showers"}]})
    )
    client = Mem0Client(base_url="http://mem0-api:8000")
    results = client.search(user_id="42", query="cold")
    assert results == [{"memory": "loves cold showers"}]
