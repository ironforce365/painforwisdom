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
def test_delete_calls_delete_endpoint():
    # /restart wipes a user's mem0 facts so a re-onboard starts memoryless.
    route = respx.delete("http://mem0-api:8000/memories/42").mock(
        return_value=Response(200, json={"deleted": True})
    )
    client = Mem0Client(base_url="http://mem0-api:8000")
    result = client.delete(user_id="42")
    assert result == {"deleted": True}
    assert route.called


@respx.mock
def test_search_returns_results():
    # mem0 OSS server: POST /search, scope via filters.user_id, count via top_k.
    route = respx.post("http://mem0-api:8000/search").mock(
        return_value=Response(200, json={"results": [{"memory": "loves cold showers"}]})
    )
    client = Mem0Client(base_url="http://mem0-api:8000")
    results = client.search(user_id="42", query="cold", limit=3)
    assert results == [{"memory": "loves cold showers"}]
    body = route.calls.last.request.content
    assert b'"top_k":3' in body
    assert b'"user_id":"42"' in body  # nested inside filters
