"""AnythingLLM-backed cross-post context — stub for the future.

This is intentionally a skeleton. Gonzalo runs AnythingLLM locally with
his blog posts embedded as a workspace; once that workspace stabilises,
this backend will swap in by flipping ``BLOG_CONTEXT_BACKEND=anythingllm``
without any other code change.

Expected env vars:

  - ``ANYTHINGLLM_BASE_URL``  e.g. ``http://localhost:3001``
  - ``ANYTHINGLLM_API_KEY``   bearer token issued in AnythingLLM settings
  - ``ANYTHINGLLM_WORKSPACE`` workspace slug (e.g. ``painforwisdom-blog``)

Likely endpoints (subject to AnythingLLM release version):

  - ``POST /api/v1/workspace/{slug}/chat``       — natural-language query
  - ``GET  /api/v1/workspace/{slug}/documents``  — listing
  - ``POST /api/v1/workspace/{slug}/vector-search`` — direct embedding search

For now, every method raises ``NotImplementedError`` with a clear message
so that anyone wiring this gets a useful trace instead of a silent miss.
"""
from __future__ import annotations

import os
from typing import List

from pipeline.blog_context import Reference, Topic


class AnythingLLMBackend:
    """Stub. Implement once the AnythingLLM workspace is stable."""

    def __init__(self) -> None:
        self.base_url = os.environ.get("ANYTHINGLLM_BASE_URL", "")
        self.api_key = os.environ.get("ANYTHINGLLM_API_KEY", "")
        self.workspace = os.environ.get("ANYTHINGLLM_WORKSPACE", "")

    def find_references(self, topic: str, *, limit: int = 5) -> List[Reference]:
        # TODO(blog_context): POST {base_url}/api/v1/workspace/{workspace}/chat
        # with {"message": "Have I written about <topic> before? Return JSON with
        # title, date, slug, snippet, score for each prior mention.", "mode": "query"}.
        # Parse the response and convert each entry to a Reference.
        raise NotImplementedError(
            "AnythingLLM backend not implemented yet. "
            "Set BLOG_CONTEXT_BACKEND=vault (default) or implement this stub."
        )

    def recent_topics(self, limit: int = 10) -> List[Topic]:
        # TODO(blog_context): query for "What topics have I covered most over
        # the last N posts?" and parse the JSON response into Topic objects.
        raise NotImplementedError(
            "AnythingLLM backend not implemented yet. "
            "Set BLOG_CONTEXT_BACKEND=vault (default) or implement this stub."
        )
