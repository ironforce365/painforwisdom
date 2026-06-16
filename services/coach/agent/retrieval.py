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
_doctrine_retriever = None  # doctrine-corpus retriever (Stream 3), built lazily


def _build_retriever():
    from vault_rag.builder import load_index
    from vault_rag.retriever import build_retriever

    storage_dir = Path(os.environ.get("COACH_INDEX_STORAGE_DIR", "/data/vault_rag"))
    index = load_index(storage_dir)
    return build_retriever(index, top_k=int(os.environ.get("COACH_VAULT_TOPK", "5")))


def _build_doctrine_retriever():
    from vault_rag.builder import load_index
    from vault_rag.retriever import build_retriever

    storage_dir = Path(os.environ.get("COACH_DOCTRINE_INDEX_DIR", "/data/doctrine_index"))
    index = load_index(storage_dir)
    return build_retriever(index, top_k=int(os.environ.get("COACH_DOCTRINE_TOPK", "5")))


def _get_retriever():
    global _retriever
    if _retriever is None:
        _retriever = _build_retriever()
    return _retriever


def _get_doctrine_retriever():
    global _doctrine_retriever
    if _doctrine_retriever is None:
        _doctrine_retriever = _build_doctrine_retriever()
    return _doctrine_retriever


def set_retriever_for_tests(retriever) -> None:
    """Inject a fake retriever (tests) or reset to None to force a rebuild."""
    global _retriever
    _retriever = retriever


def set_doctrine_retriever_for_tests(retriever) -> None:
    """Inject a fake doctrine retriever (tests) or reset to None."""
    global _doctrine_retriever
    _doctrine_retriever = retriever


def _node_to_dict(n) -> dict:
    return {
        "text": n.get_content(),
        "source": n.metadata.get("slug") or n.metadata.get("file_path", "unknown"),
        # Reranker scores are numpy float32; cast so downstream stays plain-Python.
        "score": float(n.score) if getattr(n, "score", None) is not None else None,
    }


def search_vault_warm(query: str, *, doctrine: bool) -> list[dict]:
    """Back the `search_vault` agent tool with the warm in-process retriever.

    Picks the doctrine retriever when the grounding gate is on (de-personalised
    principles — never raw biography, the "four months of recovery" leak) and the
    raw-vault retriever otherwise. Reuses the process-wide singleton built for
    pre-retrieval, so a mid-turn dig-deeper never re-pays the cold cross-encoder
    load the per-turn MCP stdio subprocess incurred (~30s/call). Never raises: a
    retrieval failure degrades to no results rather than taking down the turn.
    """
    try:
        retriever = _get_doctrine_retriever() if doctrine else _get_retriever()
        nodes = retriever.retrieve(query)
    except Exception:
        log.exception("warm search_vault retrieval failed; returning no results")
        return []
    return [_node_to_dict(n) for n in nodes]


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


def retrieve_for_turn_rich(text: str) -> tuple[str, list[str], dict[str, str]]:
    """Retrieve vault context for a turn → (context_block, source_slugs, slug_text).

    ``slug_text`` maps each slug to its retrieved chunk text so the grounding
    judge can check entailment against real source text (Stream 2 plumbing —
    slug-only sources carried ``text=""`` and were unverifiable). Blank chunks
    are omitted from the map.

    Never raises: a retrieval failure degrades the turn to no-context (the system
    prompt handles "nothing relevant") rather than taking down the coach.
    """
    try:
        nodes = _get_retriever().retrieve(text)
    except Exception:
        log.exception("vault pre-retrieval failed; proceeding without context")
        return "", [], {}
    results = [_node_to_dict(n) for n in nodes]
    slugs = [r["source"] for r in results if r.get("source")]
    slug_text: dict[str, str] = {}
    for r in results:
        chunk = (r.get("text") or "").strip()
        if r.get("source") and chunk:
            slug_text.setdefault(r["source"], chunk)
    return format_vault_context(results), slugs, slug_text


def retrieve_for_turn(text: str) -> tuple[str, list[str]]:
    """Back-compat two-tuple wrapper around :func:`retrieve_for_turn_rich`."""
    block, slugs, _ = retrieve_for_turn_rich(text)
    return block, slugs


def format_doctrine_context(results: list[dict]) -> str:
    """Render distilled doctrine principles as the `<doctrine>` block, or ''.

    Doctrine is teaching material — transferable principles, NOT facts about the
    user. Framed so the model reasons WITH it but never attributes it to the
    person it's talking to. Slugs omitted (same no-citation rule as the vault).
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
        "<doctrine>\n"
        "Distilled coaching principles relevant to THIS turn — transferable wisdom "
        "to reason WITH. This is teaching material, NOT facts about the person you "
        "are talking to; never attribute it to them as their own history.\n\n"
        f"{body}\n"
        "</doctrine>"
    )


def retrieve_doctrine_for_turn(text: str) -> tuple[str, list[str], str]:
    """Retrieve distilled doctrine for a turn → (block, slugs, joined_text).

    ``joined_text`` is the concatenated principle text for the grounding judge's
    D1 source. Never raises: a failure (e.g. no doctrine index yet) degrades to
    empty, and the system prompt handles "nothing relevant".
    """
    try:
        nodes = _get_doctrine_retriever().retrieve(text)
    except Exception:
        log.exception("doctrine pre-retrieval failed; proceeding without doctrine")
        return "", [], ""
    results = [_node_to_dict(n) for n in nodes]
    slugs = [r["source"] for r in results if r.get("source")]
    texts = [(r.get("text") or "").strip() for r in results]
    joined = "\n\n".join(t for t in texts if t)
    return format_doctrine_context(results), slugs, joined
