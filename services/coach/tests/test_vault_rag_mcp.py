"""MCP server exposes search_vault tool that returns ranked nodes."""
from __future__ import annotations
import os
import pytest
from pathlib import Path

from vault_rag.mcp_server import _search_vault, set_index_for_tests
from vault_rag.builder import build_index
from vault_rag.retriever import build_retriever


@pytest.mark.skipif(not os.environ.get("OPENAI_API_KEY"), reason="needs OPENAI_API_KEY")
def test_search_vault_returns_chunks(fixture_vault_dir: Path, tmp_index_dir: Path):
    idx = build_index(fixture_vault_dir, tmp_index_dir)
    set_index_for_tests(build_retriever(idx, top_k=2))
    result = _search_vault("running in the rain")
    assert isinstance(result, list)
    assert 1 <= len(result) <= 2
    assert all("text" in r and "source" in r for r in result)
