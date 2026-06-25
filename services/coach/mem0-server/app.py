"""Minimal Mem0 REST shim — exposes only the two endpoints the coach uses
(POST /memories, POST /search) over the mem0ai library, vector-only (pgvector).

Why a shim instead of mem0/mem0-api-server:
  * That image is published arm64-only (no linux/amd64 manifest), so it cannot
    run on this amd64 host — Mem0's own compose builds from source for the same
    reason.
  * Their full server adds JWT auth + a users/app DB + alembic migrations the
    coach does not need (single internal trust boundary on the compose network).

The coach only calls add + search, so this shim is the entire surface area.
Graph memory is a documented follow-up: add a `graph_store` block to the config
below + install mem0ai[graph] + a neo4j service. Stored vector memories survive
that upgrade.
"""
from __future__ import annotations

import os

from fastapi import FastAPI
from mem0 import Memory
from pydantic import BaseModel


def _build_memory() -> Memory:
    config = {
        # Pin the extraction LLM. mem0 2.x defaults to a newer OpenAI model that
        # rejects the `max_tokens` param mem0 still sends (400). gpt-4o-mini
        # accepts it, is cheap, and is plenty for fact extraction.
        "llm": {"provider": "openai", "config": {"model": "gpt-4o-mini"}},
        "embedder": {"provider": "openai", "config": {"model": "text-embedding-3-small"}},
        "vector_store": {
            "provider": "pgvector",
            "config": {
                "host": os.environ.get("POSTGRES_HOST", "mem0-postgres"),
                "port": int(os.environ.get("POSTGRES_PORT", "5432")),
                "user": os.environ["POSTGRES_USER"],
                "password": os.environ["POSTGRES_PASSWORD"],
                "dbname": os.environ.get("POSTGRES_DB", "postgres"),
                "collection_name": os.environ.get("POSTGRES_COLLECTION_NAME", "coach_memories"),
            },
        },
    }
    return Memory.from_config(config)


memory = _build_memory()
app = FastAPI(title="coach-mem0-shim")


class Message(BaseModel):
    role: str
    content: str


class AddRequest(BaseModel):
    messages: list[Message]
    user_id: str


class SearchRequest(BaseModel):
    query: str
    filters: dict = {}
    top_k: int = 5


def _as_results(out: object) -> dict:
    """mem0 returns {"results": [...]}; tolerate a bare list for forward-compat."""
    return out if isinstance(out, dict) else {"results": out}


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/memories")
def add_memories(req: AddRequest) -> dict:
    out = memory.add([m.model_dump() for m in req.messages], user_id=req.user_id)
    return _as_results(out)


@app.post("/search")
def search_memories(req: SearchRequest) -> dict:
    # mem0 2.x: entity scoping must go through `filters` (top-level user_id is
    # rejected); result count is `top_k`.
    out = memory.search(query=req.query, top_k=req.top_k, filters=req.filters or None)
    return _as_results(out)


@app.delete("/memories/{user_id}")
def delete_memories(user_id: str) -> dict:
    # Wipe ALL of one user's memories (the coach's /restart). Idempotent: a user
    # with nothing stored deletes cleanly.
    memory.delete_all(user_id=user_id)
    return {"deleted": True, "user_id": user_id}
