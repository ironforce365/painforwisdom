"""Cross-post context backend — pluggable surface for "what has Gonzalo
already said about X?".

The writer agent uses this to avoid re-explaining concepts like the aMCC,
Goggins, or the cookie jar across consecutive posts. Backends are selected
by the ``BLOG_CONTEXT_BACKEND`` env var:

  - ``vault`` (default) — scans the local Obsidian vault entries.
  - ``anythingllm``     — queries Gonzalo's AnythingLLM workspace
                          (semantic embeddings). Currently a stub; falls
                          back to ``vault`` with a warning if its env vars
                          (``ANYTHINGLLM_BASE_URL`` /
                          ``ANYTHINGLLM_API_KEY`` / ``ANYTHINGLLM_WORKSPACE``)
                          are missing.

The interface is intentionally small. Concrete backends implement two
methods; everything else (caching, ranking, fallback) is the backend's
business. Adding a new backend means: implement the protocol, register it
in ``get_backend``.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List, Protocol


@dataclass
class Reference:
    """A prior mention of a topic in past content."""

    source: str  # "vault" | "anythingllm" | other
    title: str
    slug: str
    date: str  # YYYY-MM-DD if known
    snippet: str  # short excerpt around the mention
    uri: str = ""  # absolute path or URL
    score: float = 0.0  # backend-specific relevance score (higher = better)


@dataclass
class Topic:
    """An aggregated topic across past content."""

    name: str
    count: int
    last_seen: str = ""  # YYYY-MM-DD
    aliases: List[str] = field(default_factory=list)


class BlogContextBackend(Protocol):
    """Two-method interface for cross-post context backends."""

    def find_references(self, topic: str, *, limit: int = 5) -> List[Reference]:
        """Return past mentions of ``topic``, ranked by relevance/recency."""
        ...

    def recent_topics(self, limit: int = 10) -> List[Topic]:
        """Return the most-referenced topics across all past content."""
        ...


def _have_anythingllm_env() -> bool:
    return all(
        os.environ.get(var)
        for var in ("ANYTHINGLLM_BASE_URL", "ANYTHINGLLM_API_KEY", "ANYTHINGLLM_WORKSPACE")
    )


def get_backend() -> BlogContextBackend:
    """Resolve the backend chosen by ``BLOG_CONTEXT_BACKEND`` (default: vault).

    Falls back to the vault backend with a warning if AnythingLLM is
    selected but its env vars are missing — this prevents pipeline crashes
    on misconfigured environments while still surfacing the issue in logs.
    """
    choice = (os.environ.get("BLOG_CONTEXT_BACKEND") or "vault").strip().lower()
    if choice == "anythingllm":
        if not _have_anythingllm_env():
            print(
                "[blog-context] WARN BLOG_CONTEXT_BACKEND=anythingllm but "
                "ANYTHINGLLM_BASE_URL/API_KEY/WORKSPACE not all set — "
                "falling back to vault backend."
            )
            from pipeline.blog_context.vault_backend import VaultBackend
            return VaultBackend()
        from pipeline.blog_context.anythingllm_backend import AnythingLLMBackend
        return AnythingLLMBackend()
    if choice != "vault":
        print(
            f"[blog-context] WARN unknown BLOG_CONTEXT_BACKEND={choice!r}; "
            "falling back to vault backend."
        )
    from pipeline.blog_context.vault_backend import VaultBackend
    return VaultBackend()


__all__ = ["Reference", "Topic", "BlogContextBackend", "get_backend"]
