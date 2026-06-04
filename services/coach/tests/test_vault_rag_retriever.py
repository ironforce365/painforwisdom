"""Hybrid retriever (vector + BM25) with cross-encoder rerank returns top-k."""
from __future__ import annotations
from pathlib import Path
import pytest
import os

from vault_rag.builder import build_index
from vault_rag.retriever import build_retriever


@pytest.mark.skipif(not os.environ.get("OPENAI_API_KEY"), reason="needs OPENAI_API_KEY")
def test_retriever_returns_topk_with_rerank(fixture_vault_dir: Path, tmp_index_dir: Path):
    index = build_index(fixture_vault_dir, tmp_index_dir)
    retriever = build_retriever(index, top_k=3)
    nodes = retriever.retrieve("how do I handle comfort in the rain?")
    assert 1 <= len(nodes) <= 3
    # Assert the *top* node matches — actually tests that rerank surfaced the
    # right fixture to position 0, not just that something in top-k matched.
    top = nodes[0]
    assert "rain" in top.get_content().lower() or "comfort" in top.get_content().lower()
