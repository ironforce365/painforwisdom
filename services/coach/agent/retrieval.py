"""In-process vault pre-retrieval for the coach agent.

Why this exists: the coach's entire value is grounding every reply in Gonzalo's
vault. Relying on the model to call the `search_vault` MCP tool proved
unreliable — it answered from general knowledge and never retrieved (incident
2026-06-04: every turn logged `retrieved_sources: []` while the retrieval stack
was healthy). So we retrieve deterministically here, before the agent runs, and
inject the result into the turn prompt — a grounding floor the model cannot
skip. The model may still call `search_vault` to dig deeper on a thread.

The retriever (index + cross-encoder reranker) is built once per process and
cached. That is also cheaper than the per-turn MCP stdio spawn, which reloads
the reranker on every call.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

log = logging.getLogger("coach.retrieval")

_retriever = None  # process-wide singleton; built lazily on first turn


def _build_retriever():
    from vault_rag.builder import load_index
    from vault_rag.retriever import build_retriever

    storage_dir = Path(os.environ.get("COACH_INDEX_STORAGE_DIR", "/data/vault_rag"))
    index = load_index(storage_dir)
    return build_retriever(index, top_k=int(os.environ.get("COACH_VAULT_TOPK", "5")))


def _get_retriever():
    global _retriever
    if _retriever is None:
        _retriever = _build_retriever()
    return _retriever


def set_retriever_for_tests(retriever) -> None:
    """Inject a fake retriever (tests) or reset to None to force a rebuild."""
    global _retriever
    _retriever = retriever


def _node_to_dict(n) -> dict:
    return {
        "text": n.get_content(),
        "source": n.metadata.get("slug") or n.metadata.get("file_path", "unknown"),
        # Reranker scores are numpy float32; cast so downstream stays plain-Python.
        "score": float(n.score) if getattr(n, "score", None) is not None else None,
    }


def format_vault_context(results: list[dict]) -> str:
    """Render retrieved chunks as a context block for the agent's turn prompt.

    Returns "" when there is nothing to inject. Source slugs are deliberately
    NOT included: conversation rule #1 forbids citing the vault to the user, and
    this block becomes part of the model's context, so leaking slugs risks them
    surfacing in the reply. Slugs are tracked separately for the inbox log.
    """
    blocks = []
    for i, r in enumerate(results, 1):
        text = (r.get("text") or "").strip()
        if not text:
            continue
        blocks.append(f"[{i}] {text}")
    if not blocks:
        return ""
    body = "\n\n".join(blocks)
    return (
        "<vault_context>\n"
        "Relevant excerpts from Gonzalo's knowledge base for THIS turn. "
        "Ground your reply in these; do not cite, name, or quote them as sources.\n\n"
        f"{body}\n"
        "</vault_context>"
    )


def retrieve_for_turn(text: str) -> tuple[str, list[str]]:
    """Retrieve vault context for a turn → (context_block, source_slugs).

    Never raises: a retrieval failure degrades the turn to no-context (the system
    prompt handles "nothing relevant") rather than taking down the coach.
    """
    try:
        nodes = _get_retriever().retrieve(text)
    except Exception:
        log.exception("vault pre-retrieval failed; proceeding without context")
        return "", []
    results = [_node_to_dict(n) for n in nodes]
    slugs = [r["source"] for r in results if r.get("source")]
    return format_vault_context(results), slugs
