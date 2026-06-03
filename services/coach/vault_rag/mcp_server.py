"""FastMCP stdio server exposing the vault RAG retriever as `search_vault`."""
from __future__ import annotations
import os
from pathlib import Path

from fastmcp import FastMCP

from vault_rag.builder import load_index
from vault_rag.retriever import build_retriever

mcp = FastMCP("coach-vault-rag")

_retriever = None  # set at startup or by tests


def set_index_for_tests(retriever) -> None:
    global _retriever
    _retriever = retriever


def _ensure_retriever():
    global _retriever
    if _retriever is None:
        storage_dir = Path(os.environ.get("COACH_INDEX_STORAGE_DIR", "/data/vault_rag"))
        index = load_index(storage_dir)
        _retriever = build_retriever(index, top_k=int(os.environ.get("COACH_VAULT_TOPK", "5")))
    return _retriever


def _search_vault(query: str) -> list[dict]:
    retriever = _ensure_retriever()
    nodes = retriever.retrieve(query)
    return [
        {
            "text": n.get_content(),
            "source": n.metadata.get("slug") or n.metadata.get("file_path", "unknown"),
            # The reranker sets score as a numpy float32, which FastMCP can't
            # serialize to structured output (→ "no structured output returned").
            # Cast to a plain float.
            "score": float(n.score) if getattr(n, "score", None) is not None else None,
        }
        for n in nodes
    ]


@mcp.tool()
def search_vault(query: str) -> list[dict]:
    """Search Gonzalo's coaching knowledge base. Returns up to 5 relevant chunks
    with source slugs. Call this BEFORE composing a reply on every turn."""
    return _search_vault(query)


if __name__ == "__main__":
    mcp.run(transport="stdio")
