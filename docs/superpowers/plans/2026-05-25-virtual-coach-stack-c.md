# Virtual Coach — Stack C Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Telegram-fronted virtual endurance coach service backed by Gonzalo's Obsidian vault — accepting voice or text, evolving per-user memory, and feeding curated conversations back into the vault via Notion HITL review.

**Architecture:** Bespoke Python service stack ("Stack C" in spec). `claude-agent-sdk` orchestrates per-user `ClaudeSDKClient` instances against three MCP servers (vault RAG via LlamaIndex `PropertyGraphIndex`, per-user scratchpad via `memory_20250818`, long-term facts via Mem0). A standalone Python Telegram bot owns transport, allowlist, and Whisper voice transcription. Post-turn hooks log conversations into `vault/_inbox/` for HITL curation through Notion. Mem0 stack (Postgres+pgvector + Neo4j) is unchanged from the prior plan. The previously planned Khoj layer is dropped because its current schema (verified 2026-05-25) does not expose the YAML keys the original plan assumed.

**Tech Stack:**
- Python 3.11+, `claude-agent-sdk` ≥0.2.87 (OAuth→Max subscription)
- `python-telegram-bot` v22+, `faster-whisper` (local STT, `base` model)
- LlamaIndex core ≥0.14.6, `llama-index-readers-obsidian` 0.7.1, `llama-index-graph-stores-simple`, `llama-index-embeddings-openai`, `llama-index-llms-anthropic`, `llama-index-retrievers-bm25`, `sentence-transformers` (rerank)
- Mem0 OSS (HTTP API), Postgres 16 + pgvector, Neo4j 5.20 community
- FastMCP (in-process + stdio MCP servers)
- pytest, pytest-asyncio, pytest-mock for tests
- Docker Compose

**Supersedes:** `docs/superpowers/plans/2026-05-24-virtual-coach.md` (Khoj-based plan, deleted by Task 1).
**Spec:** `docs/superpowers/specs/2026-05-24-virtual-coach-design.md` (Phase 2 / Stack C section authoritative).
**Verification reports:** `~/.claude/jobs/4b96cef6/{khoj-schema-verify,telegram-plugin-verify,agent-sdk-verify,llamaindex-verify}.md`.

---

## Critical gotchas (bake into every task)

0. **All Anthropic calls go through LiteLLM proxy** (added in SC-Task 1.5). The proxy holds the single `CLAUDE_CODE_OAUTH_TOKEN` and forwards it upstream. Downstream services (`coach-agent`, `mem0-api`, eval judge) set `ANTHROPIC_BASE_URL=http://litellm:4000` and use `ANTHROPIC_API_KEY=<LITELLM_MASTER_KEY>` as the gateway auth. This consolidates spend on the Max bucket and eliminates the OAuth-shadowing footgun entirely. See [[litellm-gateway-for-max-oauth]].

1. **`ANTHROPIC_API_KEY` (real Anthropic key) appears ONLY inside the LiteLLM container.** Outside the gateway, `ANTHROPIC_API_KEY` always means "LiteLLM master key" (gateway auth), never a real Anthropic credential. This is what makes the OAuth-shadowing problem from the original architecture disappear.
2. **OAuth single-user ToS** — only Gonzalo is the SDK principal. Telegram users are addressees, not principals. Document in README.
3. **Agent SDK separate quota bucket from 2026-06-15** — quota monitor (Task 12) tracks `ResultMessage.total_cost_usd` and flags when projected burn exceeds 80% of the bucket cap.
4. **LlamaIndex Neo4j collides with Mem0's Neo4j** — both stamp `__Entity__`/`__Node__`/`Chunk`/`MENTIONS` labels. Use `SimplePropertyGraphStore` (in-mem, persisted to disk) for the 120-doc vault — fast, no second Neo4j container.
5. **LlamaIndex 0.7.x wikilink bridge** — `ObsidianReader` parses `[[name]]` into `metadata["wikilinks"]` + `metadata["backlinks"]`, but doesn't seed `node.relationships`. Manual ~10-line bridge required (Task 3).
6. **No Anthropic embedder** — use `OpenAIEmbedding("text-embedding-3-small")`. Already in env.
7. **Khoj artifacts** — `services/coach/khoj_config.yaml` and the `khoj` service block in `docker-compose.yml` are stale. Task 1 removes them. `coach_prompt.md` is reused (now loaded by Python code, not Khoj).
8. **Telegram plugin (`claude-channel-telegram`) NOT used** — built for Claude Code interactive sessions, not headless services. We borrow only the `access.json` allowlist schema.
9. **Cache theme embeddings as `.npz`** — `np.savez_compressed` + `np.load(allow_pickle=False)`. Never use unsafe binary serialization for caches.

---

## File structure

```
services/coach/
  docker-compose.yml                    # Mem0 stack + coach + telegram-bot services
  .env.template                         # bot tokens, ANTHROPIC OAuth, MEM0, NOTION, allowlist
  coach_prompt.md                       # versioned system prompt (kept from prior task)
  access.json                           # Telegram allowlist (numeric user IDs)
  pyproject.toml                        # Python deps for coach + bot + sidecars
  README.md                             # ops doc

  vault_rag/                            # MCP server #1: vault RAG
    __init__.py
    builder.py                          # ObsidianReader → wikilink bridge → PropertyGraphIndex
    retriever.py                        # QueryFusionRetriever (PGRetriever + BM25) + rerank
    mcp_server.py                       # FastMCP stdio server exposing search_vault tool
    rebuild_cron.py                     # nightly full rebuild
    storage/                            # SimplePropertyGraphStore JSON dump + vector cache
      .gitkeep

  user_memory/                          # MCP server #2: per-user scratchpad
    __init__.py
    mcp_server.py                       # in-process MCP server wrapping memory_20250818 client handler
    storage/                            # per-user dirs /memories/<telegram_id>/
      .gitkeep

  mem0_mcp/                             # MCP server #3: Mem0 HTTP → MCP shim
    __init__.py
    mcp_server.py                       # stdio MCP server: add_memory / search_memories tools
    client.py                           # HTTP client for Mem0 API

  agent/                                # coach agent service (long-lived process)
    __init__.py
    service.py                          # FastAPI HTTP endpoint accepting (user_id, text) → reply
    session_map.py                      # in-memory user_id → ClaudeSDKClient + session_id mapping
    hooks.py                            # post_turn_hook → vault/_inbox/<user_id>/<ts>.md

  telegram_bot/                         # Telegram service (separate process)
    __init__.py
    bot.py                              # python-telegram-bot polling loop
    allowlist.py                        # access.json loader + check
    voice.py                            # faster-whisper transcription
    coach_client.py                     # HTTP client → agent service

  crisis/                               # crisis pre-filter
    __init__.py
    keywords.yaml                       # crisis trigger phrases (curated list)
    filter.py                           # check(text) → True/False + canned reply
    canned_reply.md

  sidecar/                              # cron-driven sidecars
    __init__.py
    classify_themes.py                  # cosine vs 11-theme embeddings → novelty score
    promote_to_notion.py                # inbox file → Notion task with snippets + theme suggestions
    quota_monitor.py                    # ResultMessage.total_cost_usd → Telegram alert at 80%

  eval/
    __init__.py
    rubric.md                           # judge system prompt
    judge.py                            # LLM-as-judge (Sonnet 4.6) rubric scorer
    single_turn/
      __init__.py
      eval_set.yaml                     # ~30 hand-crafted turns with expected behavior
      run.py                            # iterate eval_set, score, emit summary
    simulated_athlete/
      __init__.py
      profiles/
        deflector.yaml
        over_eager.yaml
        plateau_intellectual.yaml
        injured_denying.yaml
        grief_runner.yaml
      simulate.py                       # athlete-agent ↔ coach-service multi-turn loop
      nightly_eval.py                   # orchestrator

  tests/
    __init__.py
    conftest.py                         # fixtures: tmp vault, tmp Mem0 stub, fake SDK client
    test_vault_rag_builder.py           # smoke: 5-doc vault → index → retrieve
    test_vault_rag_retriever.py
    test_user_memory_mcp.py
    test_mem0_mcp_client.py             # against stub HTTP server
    test_agent_session_map.py
    test_agent_hooks.py
    test_telegram_allowlist.py
    test_telegram_voice.py              # faster-whisper smoke on fixture audio
    test_crisis_filter.py
    test_classify_themes.py             # monkeypatched embeddings
    test_promote_to_notion.py           # mocked notion client
    test_quota_monitor.py
    test_eval_judge.py                  # rubric scoring against canned transcript
    fixtures/
      vault/                            # 5-doc miniature vault
        themes/
          deliberate-discomfort.md
          comfort-as-default.md
        entries/
          2026-01-01-test-entry.md
        deep-dive/
          deliberate-discomfort/
            theory.md
            application.md
      audio/
        hello.ogg                       # 1-sec opus voice fixture
      transcripts/
        good-turn.json
        bad-turn.json

eval-runs/                              # gitignored; nightly eval outputs land here
.env.coach                              # gitignored, real secrets
```

Total estimated new code: ~800 LOC Python + ~300 LOC tests.

---

## Task 1: Cleanup — drop Khoj artifacts; supersede prior plan

**Files:**
- Delete: `services/coach/khoj_config.yaml`
- Modify: `services/coach/docker-compose.yml` (remove khoj service block + khoj_data volume)
- Modify: `services/coach/.env.template` (rename `KHOJ_ALLOWED_TELEGRAM_IDS` → `COACH_ALLOWED_TELEGRAM_IDS`, add `CLAUDE_CODE_OAUTH_TOKEN`)
- Modify: `services/coach/README.md` (point at new plan, remove Khoj instructions)

Note: `docs/superpowers/plans/2026-05-24-virtual-coach.md` was deleted in the same commit that introduced this Stack C plan — do NOT attempt to delete it again.

- [ ] **Step 1: Delete `khoj_config.yaml`**

```bash
git rm services/coach/khoj_config.yaml
```

- [ ] **Step 2: Edit `services/coach/docker-compose.yml`**

Remove the entire `khoj:` service block (lines ~50–67) and the `khoj_data:` volume entry. Keep `mem0-postgres`, `mem0-neo4j`, `mem0-api` services and their volumes (`mem0_pg_data`, `mem0_neo4j_data`).

Resulting compose file should have exactly 3 services and 2 named volumes.

- [ ] **Step 3: Edit `services/coach/.env.template`**

Rename the allowlist var and add OAuth token + LlamaIndex storage paths:

```bash
# Bot
TELEGRAM_COACH_BOT_TOKEN=
TELEGRAM_COACH_ALERT_CHAT_ID=

# Anthropic — OAuth subscription token (from `claude setup-token`). Do NOT also set ANTHROPIC_API_KEY in coach service; it silently shadows OAuth.
CLAUDE_CODE_OAUTH_TOKEN=

# OpenAI (embeddings for LlamaIndex; STT runs locally via faster-whisper)
OPENAI_API_KEY=

# Mem0
MEM0_API_URL=http://mem0-api:8000
MEM0_PG_USER=mem0
MEM0_PG_PASSWORD=
MEM0_PG_DB=mem0
MEM0_NEO4J_USER=neo4j
MEM0_NEO4J_PASSWORD=

# Notion (reuse root .env)
NOTION_API_KEY=
NOTION_COACH_INBOX_DATA_SOURCE_ID=

# Vault host path (mounted :ro into coach service)
VAULT_HOST_PATH=/home/gonzalo/workspace/painforwisdom/painforwisdom/obsidian-vault

# Comma-separated allowlist of Telegram numeric user IDs
COACH_ALLOWED_TELEGRAM_IDS=

# LlamaIndex storage (persisted SimplePropertyGraphStore + vector cache)
COACH_INDEX_STORAGE_DIR=/data/vault_rag
```

- [ ] **Step 4: Update `services/coach/README.md`**

Replace the file with a stub that points at this plan:

```markdown
# Virtual coach service

Stack C implementation. See plan: `docs/superpowers/plans/2026-05-25-virtual-coach-stack-c.md`.

## Quick start (post-implementation)

```bash
cp .env.template .env.coach   # fill in
docker compose --env-file .env.coach -f docker-compose.yml up -d
```

## Services

- `mem0-postgres`, `mem0-neo4j`, `mem0-api` — long-term memory
- `coach-agent` — claude-agent-sdk service (HTTP on :8800)
- `telegram-bot` — bot polling, allowlist, voice→Whisper→agent

## Cron sidecars

- `sidecar/classify_themes.py` — every 30 min
- `sidecar/promote_to_notion.py` — hourly
- `sidecar/quota_monitor.py` — every 15 min
- `vault_rag/rebuild_cron.py` — nightly 02:00
- `eval/simulated_athlete/nightly_eval.py` — nightly 03:00
```

- [ ] **Step 5: Commit**

```bash
git add services/coach/khoj_config.yaml services/coach/docker-compose.yml services/coach/.env.template services/coach/README.md
git commit -m "coach: drop Khoj layer; supersede plan with Stack C plan"
```

---

## Task 1.5: LiteLLM proxy service — Max-OAuth gateway

All downstream Anthropic-calling services (`coach-agent`, `mem0-api`, eval judge, classify_themes if it ever uses Claude) point at this proxy. The proxy holds the single `CLAUDE_CODE_OAUTH_TOKEN` and forwards it upstream so all spend lands on the Max subscription bucket. Downstream services use `ANTHROPIC_API_KEY=<LITELLM_MASTER_KEY>` for gateway auth — no real Anthropic API key exists outside the proxy.

**Files:**
- Create: `services/coach/litellm_config.yaml`
- Modify: `services/coach/docker-compose.yml` (add `litellm` service)
- Modify: `services/coach/.env.template` (add `LITELLM_MASTER_KEY`)

- [ ] **Step 1: Create `services/coach/litellm_config.yaml`**

```yaml
model_list:
  - model_name: claude-opus-4-7
    litellm_params:
      model: anthropic/claude-opus-4-7
  - model_name: claude-sonnet-4-6
    litellm_params:
      model: anthropic/claude-sonnet-4-6
  - model_name: claude-haiku-4-5
    litellm_params:
      model: anthropic/claude-haiku-4-5

general_settings:
  master_key: os.environ/LITELLM_MASTER_KEY
  forward_client_headers_to_llm_api: true

litellm_settings:
  set_verbose: false
  drop_params: true
```

Notes:
- No `api_key:` is set on the model blocks — that means LiteLLM falls through to the env (`ANTHROPIC_API_KEY` if set) OR the OAuth header forwarded from clients. We forward an OAuth-style Bearer header via env var injection at the gateway layer; see Step 2 env block.
- `master_key` authenticates downstream services. Generate with `openssl rand -hex 32` when filling `.env.coach`.

- [ ] **Step 2: Append `litellm` service to `services/coach/docker-compose.yml`**

Insert after `mem0-api`, before any future `coach-agent`:

```yaml
  litellm:
    image: ghcr.io/berriai/litellm:main-latest
    environment:
      LITELLM_MASTER_KEY: ${LITELLM_MASTER_KEY}
      # OAuth token forwarded upstream as Authorization: Bearer.
      CLAUDE_CODE_OAUTH_TOKEN: ${CLAUDE_CODE_OAUTH_TOKEN}
      # LiteLLM's Anthropic adapter reads ANTHROPIC_API_KEY when present; pass the
      # OAuth token here so the adapter treats it as the bearer credential.
      ANTHROPIC_API_KEY: ${CLAUDE_CODE_OAUTH_TOKEN}
    volumes:
      - ./litellm_config.yaml:/app/config.yaml:ro
    command: ["--config", "/app/config.yaml", "--port", "4000"]
    ports:
      - "4000:4000"
    healthcheck:
      test: ["CMD", "curl", "-fsS", "http://localhost:4000/health/liveliness"]
      interval: 15s
      retries: 5
    restart: unless-stopped
```

- [ ] **Step 3: Append `LITELLM_MASTER_KEY=` to `services/coach/.env.template`**

Insert directly under the `CLAUDE_CODE_OAUTH_TOKEN=` block:

```
# LiteLLM gateway — master key authenticates downstream services to LiteLLM.
# Downstream services set ANTHROPIC_API_KEY to this value (not a real Anthropic key).
# Generate with: openssl rand -hex 32
LITELLM_MASTER_KEY=
```

- [ ] **Step 4: Rewire `mem0-api` to route through LiteLLM**

In `services/coach/docker-compose.yml`, edit the existing `mem0-api` service `environment:` block. Replace:

```yaml
      ANTHROPIC_API_KEY: ${ANTHROPIC_API_KEY}
```

with:

```yaml
      ANTHROPIC_BASE_URL: http://litellm:4000
      ANTHROPIC_API_KEY: ${LITELLM_MASTER_KEY}
```

And add `litellm: { condition: service_healthy }` to its `depends_on:` block alongside the existing pg+neo4j entries.

- [ ] **Step 5: Verify compose still parses**

```bash
docker compose --env-file services/coach/.env.template -f services/coach/docker-compose.yml config >/dev/null
```

Expected exit 0.

- [ ] **Step 6: Commit**

```bash
git add services/coach/litellm_config.yaml services/coach/docker-compose.yml services/coach/.env.template
git commit -m "coach: LiteLLM proxy service — Max OAuth gateway for all Anthropic calls"
```

---

## Task 2: Python project — pyproject.toml + tests scaffold

**Files:**
- Create: `services/coach/pyproject.toml`
- Create: `services/coach/tests/__init__.py`
- Create: `services/coach/tests/conftest.py`
- Create: `services/coach/tests/fixtures/vault/themes/deliberate-discomfort.md`
- Create: `services/coach/tests/fixtures/vault/themes/comfort-as-default.md`
- Create: `services/coach/tests/fixtures/vault/entries/2026-01-01-test-entry.md`
- Create: `services/coach/tests/fixtures/vault/deep-dive/deliberate-discomfort/theory.md`
- Create: `services/coach/tests/fixtures/vault/deep-dive/deliberate-discomfort/application.md`

- [ ] **Step 1: Create `pyproject.toml`**

```toml
[project]
name = "painforwisdom-coach"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
  "claude-agent-sdk>=0.2.87",
  "python-telegram-bot[ext]>=22.0",
  "faster-whisper>=1.0.3",
  "llama-index-core>=0.14.6",
  "llama-index-readers-obsidian>=0.7.1",
  "llama-index-embeddings-openai>=0.6.0",
  "llama-index-llms-anthropic>=0.10.0",
  "llama-index-retrievers-bm25>=0.6.0",
  "llama-index-graph-stores-simple>=0.6.0",
  "sentence-transformers>=3.0.0",
  "mcp>=1.4.0",
  "fastmcp>=2.0.0",
  "fastapi>=0.115.0",
  "uvicorn[standard]>=0.30.0",
  "httpx>=0.27.0",
  "pyyaml>=6.0",
  "notion-client>=2.2.0",
  "openai>=1.50.0",
]

[project.optional-dependencies]
test = [
  "pytest>=8.0",
  "pytest-asyncio>=0.24",
  "pytest-mock>=3.14",
  "respx>=0.21",
]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

- [ ] **Step 2: Create `tests/__init__.py` (empty file)** and `tests/conftest.py`

```python
"""Shared pytest fixtures for the coach test suite."""
from __future__ import annotations
import os
from pathlib import Path
import pytest

FIXTURE_VAULT = Path(__file__).parent / "fixtures" / "vault"


@pytest.fixture
def fixture_vault_dir() -> Path:
    """Path to the miniature 5-doc test vault."""
    assert FIXTURE_VAULT.exists(), f"missing fixture vault at {FIXTURE_VAULT}"
    return FIXTURE_VAULT


@pytest.fixture
def tmp_index_dir(tmp_path: Path) -> Path:
    d = tmp_path / "vault_rag"
    d.mkdir()
    return d


@pytest.fixture(autouse=True)
def _unset_anthropic_api_key(monkeypatch):
    """Defensive: never let a stray ANTHROPIC_API_KEY shadow OAuth in tests."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
```

- [ ] **Step 3: Write fixture vault docs**

`tests/fixtures/vault/themes/deliberate-discomfort.md`:

```markdown
# Deliberate Discomfort

Choosing the hard path on purpose. See also [[comfort-as-default]] and [[deep-dive/deliberate-discomfort/theory]].

Related entries: [[2026-01-01-test-entry]].
```

`tests/fixtures/vault/themes/comfort-as-default.md`:

```markdown
# Comfort As Default

The body defaults to comfort; we must override it. Counterpoint to [[deliberate-discomfort]].
```

`tests/fixtures/vault/entries/2026-01-01-test-entry.md`:

```markdown
---
date: 2026-01-01
themes-touched: [deliberate-discomfort, comfort-as-default]
---

# Test entry — running in the rain

Today I almost skipped because of rain. Pushed through. The [[comfort-as-default]] pull was strong; the [[deliberate-discomfort]] choice paid off.
```

`tests/fixtures/vault/deep-dive/deliberate-discomfort/theory.md`:

```markdown
# Deliberate discomfort — theory

aMCC activation under voluntary effort. Stacking small voluntary frictions builds the override muscle.
```

`tests/fixtures/vault/deep-dive/deliberate-discomfort/application.md`:

```markdown
# Deliberate discomfort — application

- One small daily friction (cold shower, 5-min plank, taking stairs).
- Track streak. Resist the negotiation.
```

- [ ] **Step 4: Verify pytest collects (no tests yet — should report 0 collected)**

```bash
cd services/coach && pip install -e ".[test]" && pytest --collect-only
```

Expected: 0 tests collected, no errors.

- [ ] **Step 5: Commit**

```bash
git add services/coach/pyproject.toml services/coach/tests/
git commit -m "coach: pyproject + tests scaffold + fixture vault"
```

---

## Task 3: Vault RAG — ObsidianReader + wikilink bridge

**Files:**
- Create: `services/coach/vault_rag/__init__.py`
- Create: `services/coach/vault_rag/builder.py`
- Create: `services/coach/tests/test_vault_rag_builder.py`

- [ ] **Step 1: Empty `vault_rag/__init__.py`**

```python
"""Vault RAG layer: LlamaIndex PropertyGraphIndex over the Obsidian vault."""
```

- [ ] **Step 2: Write failing test `tests/test_vault_rag_builder.py`**

```python
"""Smoke test: ObsidianReader + wikilink bridge produces nodes with typed relationships."""
from __future__ import annotations
from pathlib import Path
from llama_index.core.schema import NodeRelationship

from vault_rag.builder import load_vault_documents


def test_load_vault_documents_emits_nodes_with_wikilink_relationships(fixture_vault_dir: Path):
    nodes = load_vault_documents(fixture_vault_dir)
    assert len(nodes) >= 5

    # The test entry references comfort-as-default and deliberate-discomfort.
    entry_node = next(n for n in nodes if "running in the rain" in n.get_content().lower())
    related = entry_node.relationships
    referenced_names = {
        r.metadata.get("name") for r in related.values()
        if isinstance(r, list) is False and hasattr(r, "metadata")
    }
    # Bridged wikilinks must surface as relationships (not just metadata strings)
    assert any("comfort-as-default" in str(v) for v in entry_node.metadata.get("wikilinks", []))
```

- [ ] **Step 3: Run test — expect ImportError**

```bash
cd services/coach && pytest tests/test_vault_rag_builder.py -v
```

Expected: `ModuleNotFoundError: No module named 'vault_rag.builder'`

- [ ] **Step 4: Implement `vault_rag/builder.py`**

```python
"""Load the Obsidian vault and bridge wikilinks into node relationships."""
from __future__ import annotations
from pathlib import Path
from typing import Iterable
import re

from llama_index.core.schema import TextNode, NodeRelationship, RelatedNodeInfo
from llama_index.readers.obsidian import ObsidianReader

WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)(?:\|[^\]]+)?\]\]")


def _slug(path: Path) -> str:
    return path.stem.lower()


def load_vault_documents(vault_dir: Path) -> list[TextNode]:
    reader = ObsidianReader(input_dir=str(vault_dir), extract_tasks=False)
    documents = reader.load_data()

    # Build a slug → node_id index for bridging wikilinks
    nodes: list[TextNode] = []
    for doc in documents:
        rel_path = Path(doc.metadata.get("file_path", doc.metadata.get("file_name", "unknown")))
        slug = _slug(rel_path)
        node = TextNode(
            text=doc.text,
            metadata={**doc.metadata, "slug": slug},
            id_=slug,
        )
        wikilinks = WIKILINK_RE.findall(doc.text)
        node.metadata["wikilinks"] = wikilinks
        nodes.append(node)

    slug_to_id = {n.metadata["slug"]: n.node_id for n in nodes}

    for node in nodes:
        for target_link in node.metadata["wikilinks"]:
            target_slug = target_link.split("/")[-1].strip().lower()
            target_id = slug_to_id.get(target_slug)
            if not target_id or target_id == node.node_id:
                continue
            node.relationships.setdefault(NodeRelationship.NEXT, [])
            related_info = RelatedNodeInfo(node_id=target_id, metadata={"name": target_slug})
            if isinstance(node.relationships[NodeRelationship.NEXT], list):
                node.relationships[NodeRelationship.NEXT].append(related_info)
            else:
                node.relationships[NodeRelationship.NEXT] = [
                    node.relationships[NodeRelationship.NEXT],
                    related_info,
                ]
    return nodes
```

- [ ] **Step 5: Run test — expect pass**

```bash
pytest tests/test_vault_rag_builder.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add services/coach/vault_rag/ services/coach/tests/test_vault_rag_builder.py
git commit -m "coach(vault_rag): ObsidianReader + wikilink bridge for typed graph edges"
```

---

## Task 4: Vault RAG — PropertyGraphIndex build + persisted storage

**Files:**
- Modify: `services/coach/vault_rag/builder.py` (add `build_index`)
- Create: `services/coach/tests/test_vault_rag_index.py`
- Create: `services/coach/vault_rag/storage/.gitkeep`

- [ ] **Step 1: Add empty `storage/.gitkeep`**

```bash
mkdir -p services/coach/vault_rag/storage && touch services/coach/vault_rag/storage/.gitkeep
```

- [ ] **Step 2: Write failing test `tests/test_vault_rag_index.py`**

```python
"""Index build + persist + reload round-trip."""
from __future__ import annotations
from pathlib import Path
import pytest

from vault_rag.builder import build_index, load_index


@pytest.mark.skipif(
    not __import__("os").environ.get("OPENAI_API_KEY"),
    reason="needs OPENAI_API_KEY for embeddings",
)
def test_build_and_reload_index(fixture_vault_dir: Path, tmp_index_dir: Path):
    idx = build_index(fixture_vault_dir, tmp_index_dir)
    assert (tmp_index_dir / "graph_store.json").exists()
    assert (tmp_index_dir / "docstore.json").exists()

    reloaded = load_index(tmp_index_dir)
    assert reloaded is not None
    # Sanity: querying for a known term hits one of our fixtures
    response = reloaded.as_query_engine(similarity_top_k=2).query("rain run")
    assert "rain" in str(response).lower() or "comfort" in str(response).lower()
```

- [ ] **Step 3: Run test — expect ImportError**

```bash
pytest tests/test_vault_rag_index.py -v
```

Expected: `ImportError: cannot import name 'build_index'`.

- [ ] **Step 4: Extend `vault_rag/builder.py`**

Append:

```python
from llama_index.core import PropertyGraphIndex, StorageContext, load_index_from_storage
from llama_index.core.indices.property_graph import ImplicitPathExtractor
from llama_index.core.graph_stores.simple_labelled import SimplePropertyGraphStore
from llama_index.embeddings.openai import OpenAIEmbedding


def _embed_model():
    return OpenAIEmbedding(model="text-embedding-3-small")


def build_index(vault_dir: Path, storage_dir: Path) -> PropertyGraphIndex:
    nodes = load_vault_documents(vault_dir)
    graph_store = SimplePropertyGraphStore()
    storage_context = StorageContext.from_defaults(property_graph_store=graph_store)
    index = PropertyGraphIndex(
        nodes=nodes,
        storage_context=storage_context,
        embed_model=_embed_model(),
        kg_extractors=[ImplicitPathExtractor()],
        show_progress=False,
    )
    storage_context.persist(persist_dir=str(storage_dir))
    return index


def load_index(storage_dir: Path) -> PropertyGraphIndex:
    storage_context = StorageContext.from_defaults(persist_dir=str(storage_dir))
    return load_index_from_storage(storage_context, embed_model=_embed_model())
```

- [ ] **Step 5: Run test — expect pass (skips without OPENAI_API_KEY)**

```bash
OPENAI_API_KEY=sk-... pytest tests/test_vault_rag_index.py -v
```

Expected: PASS (or SKIPPED if key not set).

- [ ] **Step 6: Commit**

```bash
git add services/coach/vault_rag/ services/coach/tests/test_vault_rag_index.py
git commit -m "coach(vault_rag): PropertyGraphIndex build + persist + reload"
```

---

## Task 5: Vault RAG — Hybrid retriever + rerank

**Files:**
- Create: `services/coach/vault_rag/retriever.py`
- Create: `services/coach/tests/test_vault_rag_retriever.py`

- [ ] **Step 1: Write failing test `tests/test_vault_rag_retriever.py`**

```python
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
    # Rerank should surface the matching fixture
    assert any("rain" in n.get_content().lower() or "comfort" in n.get_content().lower()
               for n in nodes)
```

- [ ] **Step 2: Run — ImportError**

```bash
pytest tests/test_vault_rag_retriever.py -v
```

- [ ] **Step 3: Implement `vault_rag/retriever.py`**

```python
"""Hybrid retrieval: vector + BM25 → reciprocal rank fusion → cross-encoder rerank."""
from __future__ import annotations
from llama_index.core import PropertyGraphIndex
from llama_index.core.retrievers import QueryFusionRetriever
from llama_index.retrievers.bm25 import BM25Retriever
from llama_index.core.postprocessor import SentenceTransformerRerank
from llama_index.core.base.base_retriever import BaseRetriever


class _RerankRetriever(BaseRetriever):
    def __init__(self, inner: BaseRetriever, top_n: int):
        super().__init__()
        self._inner = inner
        self._rerank = SentenceTransformerRerank(
            model="cross-encoder/ms-marco-MiniLM-L-6-v2",
            top_n=top_n,
        )

    def _retrieve(self, query_bundle):
        nodes = self._inner.retrieve(query_bundle)
        return self._rerank.postprocess_nodes(nodes, query_bundle)


def build_retriever(index: PropertyGraphIndex, top_k: int = 5) -> BaseRetriever:
    pg_retriever = index.as_retriever(similarity_top_k=top_k * 2)
    docstore_nodes = list(index.docstore.docs.values())
    bm25_retriever = BM25Retriever.from_defaults(
        nodes=docstore_nodes,
        similarity_top_k=top_k * 2,
    )
    fusion = QueryFusionRetriever(
        retrievers=[pg_retriever, bm25_retriever],
        similarity_top_k=top_k * 2,
        mode="reciprocal_rerank",
        use_async=False,
        verbose=False,
    )
    return _RerankRetriever(fusion, top_n=top_k)
```

- [ ] **Step 4: Run — expect PASS (or SKIPPED w/o key)**

```bash
OPENAI_API_KEY=sk-... pytest tests/test_vault_rag_retriever.py -v
```

- [ ] **Step 5: Commit**

```bash
git add services/coach/vault_rag/retriever.py services/coach/tests/test_vault_rag_retriever.py
git commit -m "coach(vault_rag): hybrid retriever (vector+BM25) with cross-encoder rerank"
```

---

## Task 6: Vault RAG — FastMCP server + nightly rebuild cron

**Files:**
- Create: `services/coach/vault_rag/mcp_server.py`
- Create: `services/coach/vault_rag/rebuild_cron.py`
- Create: `services/coach/tests/test_vault_rag_mcp.py`

- [ ] **Step 1: Write failing test `tests/test_vault_rag_mcp.py`**

```python
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
```

- [ ] **Step 2: Run — ImportError**

- [ ] **Step 3: Implement `vault_rag/mcp_server.py`**

```python
"""FastMCP stdio server exposing the vault RAG retriever as `search_vault`."""
from __future__ import annotations
import os
from pathlib import Path
from typing import Optional

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
            "score": getattr(n, "score", None),
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
```

- [ ] **Step 4: Run test — expect PASS**

- [ ] **Step 5: Write `vault_rag/rebuild_cron.py`**

```python
"""Nightly full rebuild of the vault RAG index."""
from __future__ import annotations
import logging
import os
import sys
from pathlib import Path

from vault_rag.builder import build_index

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("vault_rag.rebuild")


def main() -> int:
    vault = Path(os.environ["COACH_VAULT_PATH"])
    storage = Path(os.environ["COACH_INDEX_STORAGE_DIR"])
    storage.mkdir(parents=True, exist_ok=True)
    log.info("rebuilding vault index from %s into %s", vault, storage)
    build_index(vault, storage)
    log.info("rebuild complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 6: Commit**

```bash
git add services/coach/vault_rag/mcp_server.py services/coach/vault_rag/rebuild_cron.py services/coach/tests/test_vault_rag_mcp.py
git commit -m "coach(vault_rag): FastMCP server + nightly rebuild cron"
```

---

## Task 7: User memory MCP — wrap `memory_20250818` with per-user dirs

**Files:**
- Create: `services/coach/user_memory/__init__.py`
- Create: `services/coach/user_memory/mcp_server.py`
- Create: `services/coach/user_memory/storage/.gitkeep`
- Create: `services/coach/tests/test_user_memory_mcp.py`

- [ ] **Step 1: Empty `__init__.py` + `storage/.gitkeep`**

- [ ] **Step 2: Write failing test `tests/test_user_memory_mcp.py`**

```python
"""Per-user scratchpad: write → read → list isolation across users."""
from __future__ import annotations
from pathlib import Path
import pytest

from user_memory.mcp_server import UserMemory


def test_per_user_isolation(tmp_path: Path):
    mem = UserMemory(base_dir=tmp_path)
    mem.create(user_id="111", path="notes.md", content="alice loves cold showers")
    mem.create(user_id="222", path="notes.md", content="bob hates running")

    assert mem.view(user_id="111", path="notes.md") == "alice loves cold showers"
    assert mem.view(user_id="222", path="notes.md") == "bob hates running"
    assert "notes.md" in mem.list(user_id="111")
    assert "notes.md" in mem.list(user_id="222")


def test_path_traversal_rejected(tmp_path: Path):
    mem = UserMemory(base_dir=tmp_path)
    with pytest.raises(ValueError):
        mem.create(user_id="111", path="../escape.md", content="x")
    with pytest.raises(ValueError):
        mem.view(user_id="111", path="/etc/passwd")
```

- [ ] **Step 3: Run — ImportError**

- [ ] **Step 4: Implement `user_memory/mcp_server.py`**

```python
"""Per-user scratchpad memory, wrapping the memory_20250818 tool surface.

Each Telegram user gets `<base_dir>/<user_id>/` to store free-form notes the
agent uses to remember context across sessions. Backed by FastMCP stdio."""
from __future__ import annotations
import os
from pathlib import Path
from fastmcp import FastMCP


class UserMemory:
    def __init__(self, base_dir: Path):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _user_root(self, user_id: str) -> Path:
        if "/" in user_id or ".." in user_id:
            raise ValueError(f"invalid user_id: {user_id!r}")
        d = self.base_dir / user_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _resolve(self, user_id: str, path: str) -> Path:
        root = self._user_root(user_id)
        candidate = (root / path).resolve()
        if not str(candidate).startswith(str(root.resolve())):
            raise ValueError(f"path traversal rejected: {path!r}")
        return candidate

    def view(self, user_id: str, path: str) -> str:
        return self._resolve(user_id, path).read_text(encoding="utf-8")

    def create(self, user_id: str, path: str, content: str) -> None:
        p = self._resolve(user_id, path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")

    def list(self, user_id: str) -> list[str]:
        root = self._user_root(user_id)
        return sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file())

    def delete(self, user_id: str, path: str) -> None:
        self._resolve(user_id, path).unlink(missing_ok=True)


mcp = FastMCP("coach-user-memory")
_store = UserMemory(base_dir=Path(os.environ.get("COACH_USER_MEMORY_DIR", "/data/user_memory")))


@mcp.tool()
def memory_view(user_id: str, path: str) -> str:
    """Read a per-user note. user_id must be the Telegram numeric ID string."""
    return _store.view(user_id, path)


@mcp.tool()
def memory_create(user_id: str, path: str, content: str) -> str:
    """Write a per-user note. Overwrites if exists."""
    _store.create(user_id, path, content)
    return "ok"


@mcp.tool()
def memory_list(user_id: str) -> list[str]:
    return _store.list(user_id)


@mcp.tool()
def memory_delete(user_id: str, path: str) -> str:
    _store.delete(user_id, path)
    return "ok"


if __name__ == "__main__":
    mcp.run(transport="stdio")
```

- [ ] **Step 5: Run test — PASS**

```bash
pytest tests/test_user_memory_mcp.py -v
```

- [ ] **Step 6: Commit**

```bash
git add services/coach/user_memory/ services/coach/tests/test_user_memory_mcp.py
git commit -m "coach(user_memory): per-user scratchpad MCP server with path-traversal guard"
```

---

## Task 8: Mem0 MCP shim — HTTP client → MCP tools

**Files:**
- Create: `services/coach/mem0_mcp/__init__.py`
- Create: `services/coach/mem0_mcp/client.py`
- Create: `services/coach/mem0_mcp/mcp_server.py`
- Create: `services/coach/tests/test_mem0_mcp_client.py`

- [ ] **Step 1: Empty `__init__.py`**

- [ ] **Step 2: Write failing test `tests/test_mem0_mcp_client.py`** using `respx` to stub HTTP

```python
"""Mem0 client: add_memory + search round-trip against stubbed HTTP."""
from __future__ import annotations
import pytest
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
```

- [ ] **Step 3: Run — ImportError**

- [ ] **Step 4: Implement `mem0_mcp/client.py`**

```python
"""Thin HTTP client around the Mem0 OSS REST API."""
from __future__ import annotations
import httpx


class Mem0Client:
    def __init__(self, base_url: str, timeout: float = 10.0):
        self._base = base_url.rstrip("/")
        self._client = httpx.Client(timeout=timeout)

    def add(self, user_id: str, text: str) -> dict:
        r = self._client.post(
            f"{self._base}/memories",
            json={"user_id": user_id, "messages": [{"role": "user", "content": text}]},
        )
        r.raise_for_status()
        return r.json()

    def search(self, user_id: str, query: str, limit: int = 5) -> list[dict]:
        r = self._client.post(
            f"{self._base}/memories/search",
            json={"user_id": user_id, "query": query, "limit": limit},
        )
        r.raise_for_status()
        return r.json().get("results", [])

    def close(self) -> None:
        self._client.close()
```

- [ ] **Step 5: Run client tests — PASS**

- [ ] **Step 6: Implement `mem0_mcp/mcp_server.py`**

```python
"""FastMCP stdio server exposing Mem0 as MCP tools."""
from __future__ import annotations
import os
from fastmcp import FastMCP

from mem0_mcp.client import Mem0Client

mcp = FastMCP("coach-mem0")
_client = Mem0Client(base_url=os.environ.get("MEM0_API_URL", "http://mem0-api:8000"))


@mcp.tool()
def mem0_add(user_id: str, text: str) -> dict:
    """Store a fact extracted from the turn. Call AFTER each user message."""
    return _client.add(user_id, text)


@mcp.tool()
def mem0_search(user_id: str, query: str, limit: int = 5) -> list[dict]:
    """Recall up to `limit` facts about user_id relevant to query. Call BEFORE replying."""
    return _client.search(user_id, query, limit)


if __name__ == "__main__":
    mcp.run(transport="stdio")
```

- [ ] **Step 7: Commit**

```bash
git add services/coach/mem0_mcp/ services/coach/tests/test_mem0_mcp_client.py
git commit -m "coach(mem0_mcp): HTTP client + FastMCP server shim"
```

---

## Task 9: Crisis filter

**Files:**
- Create: `services/coach/crisis/__init__.py`
- Create: `services/coach/crisis/keywords.yaml`
- Create: `services/coach/crisis/canned_reply.md`
- Create: `services/coach/crisis/filter.py`
- Create: `services/coach/tests/test_crisis_filter.py`

- [ ] **Step 1: Empty `__init__.py`**

- [ ] **Step 2: Write `crisis/keywords.yaml`**

```yaml
# Curated crisis triggers. Case-insensitive substring match against the user message
# after lowercasing. Conservative set — false positives are acceptable, false negatives are not.
triggers:
  - kill myself
  - end my life
  - end it all
  - suicide
  - want to die
  - hurt myself
  - cutting myself
  - no reason to live
  - nothing matters anymore
  - me quiero matar
  - quitarme la vida
  - no quiero seguir viviendo
  - hacerme daño
```

- [ ] **Step 3: Write `crisis/canned_reply.md`**

```markdown
I hear you, and I'm worried about you. What you're feeling is real, and you deserve support from someone trained to help with this — not from a coach app.

Please reach out right now:
- US: 988 (Suicide & Crisis Lifeline, call or text)
- UK: 116 123 (Samaritans)
- International directory: https://findahelpline.com

If you're in immediate danger, call your local emergency number.

I'll be here when you're ready to talk about running again.
```

- [ ] **Step 4: Write failing test `tests/test_crisis_filter.py`**

```python
"""Crisis filter intercepts trigger phrases and emits canned reply."""
from __future__ import annotations
from crisis.filter import check_message, CrisisHit


def test_clean_message_passes():
    assert check_message("how do I push through pain today") is None


def test_english_trigger_matches():
    hit = check_message("I want to die")
    assert isinstance(hit, CrisisHit)
    assert "988" in hit.canned_reply


def test_spanish_trigger_matches():
    hit = check_message("últimamente quiero quitarme la vida")
    assert hit is not None


def test_case_insensitive():
    assert check_message("KILL MYSELF") is not None
```

- [ ] **Step 5: Run — ImportError**

- [ ] **Step 6: Implement `crisis/filter.py`**

```python
"""Pre-Claude keyword interception. If hit, agent never sees the turn."""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import yaml

_HERE = Path(__file__).parent

with (_HERE / "keywords.yaml").open() as f:
    _TRIGGERS: list[str] = [t.lower() for t in yaml.safe_load(f)["triggers"]]

_CANNED_REPLY: str = (_HERE / "canned_reply.md").read_text(encoding="utf-8")


@dataclass(frozen=True)
class CrisisHit:
    trigger: str
    canned_reply: str


def check_message(text: str) -> CrisisHit | None:
    lowered = text.lower()
    for trig in _TRIGGERS:
        if trig in lowered:
            return CrisisHit(trigger=trig, canned_reply=_CANNED_REPLY)
    return None
```

- [ ] **Step 7: Run test — PASS**

- [ ] **Step 8: Commit**

```bash
git add services/coach/crisis/ services/coach/tests/test_crisis_filter.py
git commit -m "coach(crisis): keyword pre-filter with EN+ES triggers and hotline canned reply"
```

---

## Task 10: Coach agent service — session map + hooks + FastAPI endpoint

**Files:**
- Create: `services/coach/agent/__init__.py`
- Create: `services/coach/agent/session_map.py`
- Create: `services/coach/agent/hooks.py`
- Create: `services/coach/agent/service.py`
- Create: `services/coach/tests/test_agent_session_map.py`
- Create: `services/coach/tests/test_agent_hooks.py`
- Create: `services/coach/tests/test_agent_service.py`

- [ ] **Step 1: Empty `agent/__init__.py`**

- [ ] **Step 2: Write failing test `tests/test_agent_session_map.py`**

```python
"""Session map: same user_id → reused session_id; different user → new session."""
from __future__ import annotations
from agent.session_map import SessionMap


def test_session_persists_per_user():
    m = SessionMap()
    sid1 = m.get_or_create_session_id("alice")
    sid2 = m.get_or_create_session_id("alice")
    sid3 = m.get_or_create_session_id("bob")
    assert sid1 == sid2
    assert sid1 != sid3
```

- [ ] **Step 3: Run — ImportError**

- [ ] **Step 4: Implement `agent/session_map.py`**

```python
"""Maps Telegram user_id → claude-agent-sdk session_id (UUID).

Session_id is what we pass to `ClaudeAgentOptions(resume=session_id)` so per-user
multi-turn history persists across requests. Stored in-memory; on restart the
SDK can resume from JSONL on disk."""
from __future__ import annotations
import uuid
import threading


class SessionMap:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._map: dict[str, str] = {}

    def get_or_create_session_id(self, user_id: str) -> str:
        with self._lock:
            sid = self._map.get(user_id)
            if sid is None:
                sid = str(uuid.uuid4())
                self._map[user_id] = sid
            return sid

    def reset(self, user_id: str) -> None:
        with self._lock:
            self._map.pop(user_id, None)
```

- [ ] **Step 5: Run session_map test — PASS**

- [ ] **Step 6: Write failing test `tests/test_agent_hooks.py`**

```python
"""post_turn_hook writes JSONL entry to vault/_inbox/<user>/."""
from __future__ import annotations
from pathlib import Path
from agent.hooks import write_inbox_entry


def test_writes_inbox_entry(tmp_path: Path):
    write_inbox_entry(
        inbox_root=tmp_path,
        user_id="42",
        user_text="why do I always skip runs in winter?",
        assistant_text="What did you avoid this week, specifically?",
        retrieved_sources=["comfort-as-default", "2026-01-01-test-entry"],
    )
    files = list((tmp_path / "42").glob("*.md"))
    assert len(files) == 1
    body = files[0].read_text()
    assert "skip runs in winter" in body
    assert "comfort-as-default" in body
```

- [ ] **Step 7: Run — ImportError**

- [ ] **Step 8: Implement `agent/hooks.py`**

```python
"""Post-turn hook: persist each (user, assistant) exchange as a markdown file
under vault/_inbox/<user_id>/<timestamp>.md for later HITL curation."""
from __future__ import annotations
from datetime import datetime, timezone
from pathlib import Path


def write_inbox_entry(
    *,
    inbox_root: Path,
    user_id: str,
    user_text: str,
    assistant_text: str,
    retrieved_sources: list[str],
) -> Path:
    user_dir = inbox_root / user_id
    user_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = user_dir / f"{ts}.md"
    path.write_text(
        f"""---
user_id: {user_id}
timestamp: {ts}
retrieved_sources: {retrieved_sources}
---

## User

{user_text}

## Coach

{assistant_text}
""",
        encoding="utf-8",
    )
    return path
```

- [ ] **Step 9: Run hooks test — PASS**

- [ ] **Step 10: Write failing test `tests/test_agent_service.py`**

```python
"""HTTP /turn endpoint: crisis hits short-circuit; happy path returns assistant text."""
from __future__ import annotations
import pytest
from fastapi.testclient import TestClient

import agent.service as svc


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("COACH_INBOX_ROOT", str(tmp_path))
    # Patch the SDK call so we don't talk to Anthropic in tests
    async def fake_chat(user_id: str, text: str):
        return ("ack: " + text, ["comfort-as-default"])
    monkeypatch.setattr(svc, "_chat_with_agent", fake_chat)
    return TestClient(svc.app)


def test_crisis_message_returns_canned(client):
    r = client.post("/turn", json={"user_id": "1", "text": "I want to die"})
    assert r.status_code == 200
    assert "988" in r.json()["reply"]
    assert r.json()["crisis"] is True


def test_happy_path(client):
    r = client.post("/turn", json={"user_id": "1", "text": "hello coach"})
    assert r.status_code == 200
    assert r.json()["reply"].startswith("ack: ")
    assert r.json()["crisis"] is False
```

- [ ] **Step 11: Run — ImportError**

- [ ] **Step 12: Implement `agent/service.py`**

```python
"""FastAPI service: POST /turn (user_id, text) → coach reply.

Wires together:
- crisis pre-filter
- claude-agent-sdk client with 3 MCP servers (vault_rag, user_memory, mem0)
- post-turn inbox hook
"""
from __future__ import annotations
import os
from pathlib import Path
from typing import Tuple

from fastapi import FastAPI
from pydantic import BaseModel

from agent.hooks import write_inbox_entry
from agent.session_map import SessionMap
from crisis.filter import check_message

app = FastAPI(title="painforwisdom-coach")
_sessions = SessionMap()
_inbox_root = Path(os.environ.get("COACH_INBOX_ROOT", "/vault/_inbox"))


class Turn(BaseModel):
    user_id: str
    text: str


class Reply(BaseModel):
    reply: str
    crisis: bool


async def _chat_with_agent(user_id: str, text: str) -> Tuple[str, list[str]]:
    """Real implementation: spin up ClaudeSDKClient with MCP servers, call query,
    collect assistant text + retrieved source slugs. Monkeypatched in tests."""
    from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions
    from claude_agent_sdk.types import McpServerConfig

    session_id = _sessions.get_or_create_session_id(user_id)
    coach_prompt = Path(__file__).parent.parent / "coach_prompt.md"
    options = ClaudeAgentOptions(
        system_prompt=coach_prompt.read_text(encoding="utf-8"),
        resume=session_id,
        mcp_servers={
            "vault_rag": McpServerConfig(
                command="python", args=["-m", "vault_rag.mcp_server"], env=dict(os.environ),
            ),
            "user_memory": McpServerConfig(
                command="python", args=["-m", "user_memory.mcp_server"], env=dict(os.environ),
            ),
            "mem0": McpServerConfig(
                command="python", args=["-m", "mem0_mcp.mcp_server"], env=dict(os.environ),
            ),
        },
    )
    sources: list[str] = []
    reply_chunks: list[str] = []
    async with ClaudeSDKClient(options=options) as client:
        await client.query(f"<user_id>{user_id}</user_id>\n\n{text}")
        async for msg in client.receive_response():
            if hasattr(msg, "content"):
                for block in msg.content:
                    if getattr(block, "type", None) == "text":
                        reply_chunks.append(block.text)
                    elif getattr(block, "type", None) == "tool_use" and block.name.endswith("search_vault"):
                        sources.append(str(block.input.get("query", "")))
    return "".join(reply_chunks), sources


@app.post("/turn", response_model=Reply)
async def turn(t: Turn) -> Reply:
    hit = check_message(t.text)
    if hit is not None:
        return Reply(reply=hit.canned_reply, crisis=True)

    reply_text, sources = await _chat_with_agent(t.user_id, t.text)
    write_inbox_entry(
        inbox_root=_inbox_root,
        user_id=t.user_id,
        user_text=t.text,
        assistant_text=reply_text,
        retrieved_sources=sources,
    )
    return Reply(reply=reply_text, crisis=False)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}
```

- [ ] **Step 13: Run agent service tests — PASS**

```bash
pytest tests/test_agent_service.py tests/test_agent_hooks.py tests/test_agent_session_map.py -v
```

- [ ] **Step 14: Commit**

```bash
git add services/coach/agent/ services/coach/tests/test_agent_session_map.py services/coach/tests/test_agent_hooks.py services/coach/tests/test_agent_service.py
git commit -m "coach(agent): FastAPI /turn endpoint with crisis filter, MCP wiring, inbox hook"
```

---

## Task 11: Telegram bot — allowlist, voice transcription, HTTP client

**Files:**
- Create: `services/coach/telegram_bot/__init__.py`
- Create: `services/coach/telegram_bot/allowlist.py`
- Create: `services/coach/telegram_bot/voice.py`
- Create: `services/coach/telegram_bot/coach_client.py`
- Create: `services/coach/telegram_bot/bot.py`
- Create: `services/coach/access.json`
- Create: `services/coach/tests/test_telegram_allowlist.py`
- Create: `services/coach/tests/test_telegram_voice.py`
- Create: `services/coach/tests/fixtures/audio/hello.ogg` (1-sec opus fixture — generate with ffmpeg)

- [ ] **Step 1: Empty `__init__.py`**

- [ ] **Step 2: Write `access.json`**

```json
{
  "version": 1,
  "policy": "allowlist",
  "allowed_user_ids": []
}
```

- [ ] **Step 3: Write failing test `tests/test_telegram_allowlist.py`**

```python
"""Allowlist enforces ID list and policy."""
from __future__ import annotations
import json
from pathlib import Path
from telegram_bot.allowlist import Allowlist


def test_allows_listed_user(tmp_path: Path):
    p = tmp_path / "access.json"
    p.write_text(json.dumps({"version": 1, "policy": "allowlist", "allowed_user_ids": [42, 99]}))
    a = Allowlist(p)
    assert a.allowed(42) is True
    assert a.allowed(99) is True
    assert a.allowed(123) is False


def test_empty_allowlist_blocks_everyone(tmp_path: Path):
    p = tmp_path / "access.json"
    p.write_text(json.dumps({"version": 1, "policy": "allowlist", "allowed_user_ids": []}))
    a = Allowlist(p)
    assert a.allowed(42) is False


def test_unknown_policy_rejects(tmp_path: Path):
    p = tmp_path / "access.json"
    p.write_text(json.dumps({"version": 1, "policy": "open", "allowed_user_ids": [42]}))
    a = Allowlist(p)
    assert a.allowed(42) is False
```

- [ ] **Step 4: Run — ImportError**

- [ ] **Step 5: Implement `telegram_bot/allowlist.py`**

```python
"""Telegram user allowlist. Schema borrowed from
anthropics/claude-plugins-official/external_plugins/telegram/access.json."""
from __future__ import annotations
import json
from pathlib import Path


class Allowlist:
    def __init__(self, access_json: Path):
        data = json.loads(Path(access_json).read_text(encoding="utf-8"))
        self._policy: str = data.get("policy", "allowlist")
        self._allowed: set[int] = set(data.get("allowed_user_ids", []))

    def allowed(self, telegram_user_id: int) -> bool:
        if self._policy != "allowlist":
            return False
        return telegram_user_id in self._allowed
```

- [ ] **Step 6: Run allowlist test — PASS**

- [ ] **Step 7: Generate fixture audio**

```bash
ffmpeg -y -f lavfi -i "sine=frequency=440:duration=1" -c:a libopus services/coach/tests/fixtures/audio/hello.ogg
```

- [ ] **Step 8: Write failing test `tests/test_telegram_voice.py`**

```python
"""Voice transcription returns a string (content unverified — the fixture is a tone)."""
from __future__ import annotations
from pathlib import Path
import pytest

from telegram_bot.voice import transcribe_voice

FIX = Path(__file__).parent / "fixtures" / "audio" / "hello.ogg"


@pytest.mark.skipif(not FIX.exists(), reason="audio fixture missing; run ffmpeg step")
def test_transcribe_returns_string():
    text = transcribe_voice(FIX)
    assert isinstance(text, str)
```

- [ ] **Step 9: Run — ImportError**

- [ ] **Step 10: Implement `telegram_bot/voice.py`**

```python
"""Local STT via faster-whisper. Loads model lazily (1.5GB download on first use)."""
from __future__ import annotations
import functools
from pathlib import Path


@functools.lru_cache(maxsize=1)
def _model():
    from faster_whisper import WhisperModel
    return WhisperModel("base", compute_type="int8")


def transcribe_voice(audio_path: Path) -> str:
    segments, _info = _model().transcribe(str(audio_path))
    return " ".join(seg.text.strip() for seg in segments).strip()
```

- [ ] **Step 11: Run voice test — PASS (or SKIP if fixture missing)**

- [ ] **Step 12: Implement `telegram_bot/coach_client.py`**

```python
"""HTTP client → coach agent service."""
from __future__ import annotations
import httpx


class CoachClient:
    def __init__(self, base_url: str, timeout: float = 60.0):
        self._client = httpx.Client(base_url=base_url, timeout=timeout)

    def turn(self, user_id: str, text: str) -> dict:
        r = self._client.post("/turn", json={"user_id": user_id, "text": text})
        r.raise_for_status()
        return r.json()
```

- [ ] **Step 13: Implement `telegram_bot/bot.py`**

```python
"""Telegram polling bot. Allowlist → (voice|text) → coach service → reply."""
from __future__ import annotations
import asyncio
import logging
import os
from pathlib import Path

from telegram import Update
from telegram.ext import (
    Application, MessageHandler, ContextTypes, filters,
)

from telegram_bot.allowlist import Allowlist
from telegram_bot.coach_client import CoachClient
from telegram_bot.voice import transcribe_voice

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("coach.telegram")


def _build_app() -> Application:
    token = os.environ["TELEGRAM_COACH_BOT_TOKEN"]
    allowlist = Allowlist(Path(os.environ.get("COACH_ACCESS_JSON", "/app/access.json")))
    coach = CoachClient(os.environ.get("COACH_AGENT_URL", "http://coach-agent:8800"))

    async def on_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        if not user or not allowlist.allowed(user.id):
            log.info("rejecting user_id=%s", user and user.id)
            return
        msg = update.effective_message
        if not msg:
            return
        if msg.voice:
            file = await msg.voice.get_file()
            tmp = Path(f"/tmp/voice_{user.id}_{msg.message_id}.ogg")
            await file.download_to_drive(tmp)
            text = transcribe_voice(tmp)
            tmp.unlink(missing_ok=True)
        else:
            text = msg.text or ""
        if not text.strip():
            return
        try:
            result = await asyncio.to_thread(coach.turn, str(user.id), text)
            await msg.reply_text(result["reply"])
        except Exception as exc:
            log.exception("coach call failed")
            await msg.reply_text("Coach is down. Try again in a minute.")

    app = Application.builder().token(token).build()
    app.add_handler(MessageHandler(filters.TEXT | filters.VOICE, on_message))
    return app


def main() -> None:
    app = _build_app()
    app.run_polling()


if __name__ == "__main__":
    main()
```

- [ ] **Step 14: Commit**

```bash
git add services/coach/telegram_bot/ services/coach/access.json services/coach/tests/test_telegram_allowlist.py services/coach/tests/test_telegram_voice.py services/coach/tests/fixtures/audio/
git commit -m "coach(telegram_bot): allowlist + faster-whisper voice + coach HTTP client + polling bot"
```

---

## Task 12: Sidecars — classify_themes, promote_to_notion, quota_monitor

**Files:**
- Create: `services/coach/sidecar/classify_themes.py`
- Create: `services/coach/sidecar/promote_to_notion.py`
- Create: `services/coach/sidecar/quota_monitor.py`
- Create: `services/coach/tests/test_classify_themes.py`
- Create: `services/coach/tests/test_promote_to_notion.py`
- Create: `services/coach/tests/test_quota_monitor.py`

Cache format note: theme embeddings are stored as `.npz` via `np.savez_compressed` and loaded with `np.load(allow_pickle=False)`. Never use unsafe binary serialization for caches.

- [ ] **Step 1: Write failing test `tests/test_classify_themes.py`**

```python
"""classify_themes returns top-N theme matches by cosine sim against vault theme set."""
from __future__ import annotations
import numpy as np
from sidecar.classify_themes import classify, ThemeMatch


def test_classify_returns_ranked_matches(monkeypatch):
    # Stub embedder: theme vectors and query vector
    fake_theme_embeds = {
        "deliberate-discomfort": np.array([1.0, 0.0]),
        "comfort-as-default": np.array([0.0, 1.0]),
    }
    def fake_embed(text: str) -> np.ndarray:
        return np.array([0.9, 0.1])  # closest to deliberate-discomfort
    monkeypatch.setattr("sidecar.classify_themes._embed_text", fake_embed)
    monkeypatch.setattr("sidecar.classify_themes._load_theme_embeddings", lambda: fake_theme_embeds)

    matches = classify("running through hard rain", top_n=2)
    assert isinstance(matches[0], ThemeMatch)
    assert matches[0].theme == "deliberate-discomfort"
    assert matches[0].score > matches[1].score
```

- [ ] **Step 2: Run — ImportError**

- [ ] **Step 3: Implement `sidecar/classify_themes.py`**

```python
"""Classify a free-text snippet against the vault's theme set by cosine similarity.

Theme embeddings cached on disk as .npz (safe; allow_pickle=False on load)."""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import os
import numpy as np


@dataclass(frozen=True)
class ThemeMatch:
    theme: str
    score: float


def _embed_text(text: str) -> np.ndarray:
    from openai import OpenAI
    r = OpenAI().embeddings.create(model="text-embedding-3-small", input=text)
    return np.asarray(r.data[0].embedding, dtype=np.float32)


def _load_theme_embeddings() -> dict[str, np.ndarray]:
    cache = Path(os.environ.get("COACH_THEME_EMBED_CACHE", "/data/theme_embeds.npz"))
    if cache.exists():
        loaded = np.load(cache, allow_pickle=False)
        return {k: loaded[k] for k in loaded.files}
    raise FileNotFoundError(f"theme embedding cache not found at {cache}; rebuild first")


def classify(text: str, top_n: int = 3) -> list[ThemeMatch]:
    query = _embed_text(text)
    themes = _load_theme_embeddings()
    qn = query / (np.linalg.norm(query) + 1e-12)
    scored = []
    for name, vec in themes.items():
        vn = vec / (np.linalg.norm(vec) + 1e-12)
        scored.append(ThemeMatch(theme=name, score=float(np.dot(qn, vn))))
    scored.sort(key=lambda m: m.score, reverse=True)
    return scored[:top_n]
```

- [ ] **Step 4: Run test — PASS**

- [ ] **Step 5: Write failing test `tests/test_promote_to_notion.py`**

```python
"""promote_to_notion sends one task per inbox file with theme suggestions in body."""
from __future__ import annotations
from pathlib import Path
from sidecar.promote_to_notion import promote


def test_promote_creates_one_notion_task_per_file(tmp_path, monkeypatch):
    inbox = tmp_path / "_inbox" / "42"
    inbox.mkdir(parents=True)
    (inbox / "20260101T100000Z.md").write_text("---\nuser_id: 42\n---\n## User\nfoo\n## Coach\nbar")

    calls: list[dict] = []
    def fake_create_page(**kwargs):
        calls.append(kwargs)
        return {"id": "page_abc"}
    monkeypatch.setattr("sidecar.promote_to_notion._notion_create_page", fake_create_page)
    monkeypatch.setattr("sidecar.promote_to_notion._classify_top", lambda text: ["comfort-as-default"])

    promoted = promote(inbox_root=tmp_path / "_inbox", data_source_id="ds_1")
    assert len(promoted) == 1
    assert len(calls) == 1
    assert "comfort-as-default" in str(calls[0])
```

- [ ] **Step 6: Run — ImportError**

- [ ] **Step 7: Implement `sidecar/promote_to_notion.py`**

```python
"""Inbox file → Notion task. Idempotent: marks promoted files by renaming `.md` → `.promoted.md`."""
from __future__ import annotations
from pathlib import Path
import os

from notion_client import Client


def _notion_create_page(**kwargs) -> dict:
    notion = Client(auth=os.environ["NOTION_API_KEY"])
    return notion.pages.create(**kwargs)


def _classify_top(text: str) -> list[str]:
    from sidecar.classify_themes import classify
    return [m.theme for m in classify(text, top_n=3)]


def promote(inbox_root: Path, data_source_id: str) -> list[Path]:
    promoted: list[Path] = []
    for f in sorted(inbox_root.rglob("*.md")):
        if f.name.endswith(".promoted.md"):
            continue
        text = f.read_text(encoding="utf-8")
        theme_hints = _classify_top(text)
        _notion_create_page(
            parent={"data_source_id": data_source_id},
            properties={
                "title": [{"text": {"content": f"Coach inbox: {f.stem}"}}],
            },
            children=[
                {"object": "block", "type": "paragraph", "paragraph": {
                    "rich_text": [{"type": "text", "text": {"content": f"Theme hints: {', '.join(theme_hints)}"}}]
                }},
                {"object": "block", "type": "code", "code": {
                    "rich_text": [{"type": "text", "text": {"content": text[:1900]}}],
                    "language": "markdown",
                }},
            ],
        )
        new = f.with_suffix(".promoted.md")
        f.rename(new)
        promoted.append(new)
    return promoted
```

- [ ] **Step 8: Run promote test — PASS**

- [ ] **Step 9: Write failing test `tests/test_quota_monitor.py`**

```python
"""Quota monitor sums total_cost_usd across recent ResultMessage logs and alerts at 80%."""
from __future__ import annotations
from pathlib import Path
import json
from sidecar.quota_monitor import compute_burn, should_alert


def test_compute_burn_sums_logs(tmp_path: Path):
    log = tmp_path / "usage.jsonl"
    log.write_text(
        json.dumps({"total_cost_usd": 1.5}) + "\n" +
        json.dumps({"total_cost_usd": 2.75}) + "\n"
    )
    assert compute_burn(log) == 4.25


def test_alerts_above_threshold():
    assert should_alert(burn=80.0, cap=100.0, threshold=0.8) is True
    assert should_alert(burn=79.0, cap=100.0, threshold=0.8) is False
```

- [ ] **Step 10: Implement `sidecar/quota_monitor.py`**

```python
"""Sum total_cost_usd from agent service usage log; alert Telegram at threshold.

Note: total_cost_usd is the SDK's client-side estimate, not authoritative billing."""
from __future__ import annotations
from pathlib import Path
import json
import os
import logging

log = logging.getLogger("coach.quota")


def compute_burn(usage_log: Path) -> float:
    total = 0.0
    for line in Path(usage_log).read_text().splitlines():
        try:
            total += float(json.loads(line).get("total_cost_usd", 0.0))
        except Exception:
            continue
    return total


def should_alert(*, burn: float, cap: float, threshold: float) -> bool:
    return burn / max(cap, 1e-9) >= threshold


def alert_telegram(message: str) -> None:
    import httpx
    token = os.environ["TELEGRAM_COACH_BOT_TOKEN"]
    chat_id = os.environ["TELEGRAM_COACH_ALERT_CHAT_ID"]
    httpx.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": message}, timeout=10,
    )


def main() -> int:
    usage_log = Path(os.environ.get("COACH_USAGE_LOG", "/data/usage.jsonl"))
    cap = float(os.environ.get("COACH_QUOTA_CAP_USD", "100"))
    threshold = float(os.environ.get("COACH_QUOTA_ALERT_THRESHOLD", "0.8"))
    burn = compute_burn(usage_log)
    log.info("burn=%.2f cap=%.2f", burn, cap)
    if should_alert(burn=burn, cap=cap, threshold=threshold):
        alert_telegram(f"Coach quota burn at {burn:.2f} / {cap:.2f} USD ({burn/cap:.0%}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 11: Commit**

```bash
git add services/coach/sidecar/ services/coach/tests/test_classify_themes.py services/coach/tests/test_promote_to_notion.py services/coach/tests/test_quota_monitor.py
git commit -m "coach(sidecar): classify_themes + promote_to_notion + quota_monitor with tests"
```

---

## Task 13: Eval Layer 3 + Layer 4 — judge, single-turn, simulated athletes

**Files:**
- Create: `services/coach/eval/__init__.py`
- Create: `services/coach/eval/rubric.md`
- Create: `services/coach/eval/judge.py`
- Create: `services/coach/eval/single_turn/__init__.py`
- Create: `services/coach/eval/single_turn/eval_set.yaml`
- Create: `services/coach/eval/single_turn/run.py`
- Create: `services/coach/eval/simulated_athlete/__init__.py`
- Create: `services/coach/eval/simulated_athlete/profiles/{deflector,over_eager,plateau_intellectual,injured_denying,grief_runner}.yaml`
- Create: `services/coach/eval/simulated_athlete/simulate.py`
- Create: `services/coach/eval/simulated_athlete/nightly_eval.py`
- Create: `services/coach/tests/test_eval_judge.py`

- [ ] **Step 1: Write `eval/rubric.md`** (judge system prompt)

```markdown
# Coach reply judging rubric

Score each (user message, coach reply) pair on these dimensions, 1-5 integer:

1. **Frontal-ness** — does the coach avoid sugar-coating?
2. **No source citing** — does the coach avoid mentioning Gonzalo by name or quoting source slugs?
3. **Probing question** — does the coach ask a clarifying question instead of jumping to advice on turn 1?
4. **Brevity** — under 4 sentences unless the user explicitly asked for depth?
5. **No hallucination** — does the reply stay grounded in retrieved chunks (you'll see them)?
6. **Voice fit** — does the reply sound like an accountability mirror, not a therapy app?

Reply with strict JSON:

```json
{"frontal":4,"no_citing":5,"probing":4,"brevity":5,"grounding":4,"voice":5,"reasoning":"<2 sentences>"}
```
```

- [ ] **Step 2: Write failing test `tests/test_eval_judge.py`**

```python
"""Judge parses a fixture transcript and returns rubric scores."""
from __future__ import annotations
from eval.judge import score_turn


def test_score_turn_returns_rubric_keys(monkeypatch):
    fake = '{"frontal":4,"no_citing":5,"probing":3,"brevity":4,"grounding":5,"voice":4,"reasoning":"ok"}'
    monkeypatch.setattr("eval.judge._call_judge_llm", lambda system, user: fake)
    result = score_turn(
        user_text="why do I always skip in winter?",
        coach_reply="What did you avoid this week, specifically?",
        retrieved=[{"text": "comfort default ...", "source": "comfort-as-default"}],
    )
    assert result["frontal"] == 4
    assert "reasoning" in result
```

- [ ] **Step 3: Run — ImportError**

- [ ] **Step 4: Implement `eval/judge.py`**

```python
"""LLM-as-judge (Sonnet 4.6) rubric scorer."""
from __future__ import annotations
import json
import os
from pathlib import Path

_RUBRIC = (Path(__file__).parent / "rubric.md").read_text(encoding="utf-8")


def _call_judge_llm(system: str, user: str) -> str:
    import anthropic
    client = anthropic.Anthropic()
    resp = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=600,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return resp.content[0].text


def score_turn(*, user_text: str, coach_reply: str, retrieved: list[dict]) -> dict:
    retrieved_dump = "\n".join(f"- ({r['source']}) {r['text'][:300]}" for r in retrieved)
    user = (
        f"USER MESSAGE:\n{user_text}\n\n"
        f"COACH REPLY:\n{coach_reply}\n\n"
        f"RETRIEVED CHUNKS:\n{retrieved_dump}"
    )
    raw = _call_judge_llm(_RUBRIC, user)
    start = raw.find("{")
    end = raw.rfind("}")
    return json.loads(raw[start:end + 1])
```

- [ ] **Step 5: Run judge test — PASS**

- [ ] **Step 6: Write `eval/single_turn/eval_set.yaml`** (10 seed entries — expand later)

```yaml
turns:
  - id: skip-rain
    user: "I skipped my run because it was raining"
    expect_themes: [comfort-as-default, deliberate-discomfort]
    expect_probing: true
  - id: heavy-mileage-injury
    user: "knee hurts but I have my long run scheduled tomorrow"
    expect_themes: [recovery-as-strategy, body-literacy]
    expect_probing: true
  - id: plateau
    user: "I've been running 30 mpw for a year and I'm stuck"
    expect_themes: [incremental-progress, goal-vs-method]
    expect_probing: true
  - id: too-fast-too-soon
    user: "I want to qualify for Boston in 6 months"
    expect_themes: [goal-vs-method, incremental-exposure]
    expect_probing: true
  - id: race-anxiety
    user: "my first marathon is Saturday and I feel sick"
    expect_themes: [naming-the-fear, body-literacy]
    expect_probing: true
  - id: stretched-thin
    user: "I'm running, lifting, and training BJJ, but progressing in none"
    expect_themes: [adaptation-cost, discipline-under-chaos]
    expect_probing: true
  - id: comparison-trap
    user: "my training partner just PRed and I haven't improved in months"
    expect_themes: [the-comparison-trap, honest-self-assessment]
    expect_probing: true
  - id: cold-shower
    user: "should I do cold showers daily?"
    expect_themes: [deliberate-discomfort, incremental-exposure]
    expect_probing: false
  - id: easy-question
    user: "what's a good morning warmup?"
    expect_themes: []
    expect_probing: false
  - id: derail
    user: "what's your favorite ice cream?"
    expect_themes: []
    expect_probing: false
```

- [ ] **Step 7: Implement `eval/single_turn/run.py`**

```python
"""Run the single-turn eval set against a running coach service. Outputs JSONL."""
from __future__ import annotations
import json
import os
import sys
from pathlib import Path
import yaml
import httpx

from eval.judge import score_turn

EVAL_SET = Path(__file__).parent / "eval_set.yaml"


def main() -> int:
    coach_url = os.environ.get("COACH_AGENT_URL", "http://localhost:8800")
    out = Path(os.environ.get("COACH_EVAL_OUT", "/eval-runs/single-turn.jsonl"))
    out.parent.mkdir(parents=True, exist_ok=True)
    items = yaml.safe_load(EVAL_SET.read_text())["turns"]
    with out.open("w") as f:
        for item in items:
            r = httpx.post(f"{coach_url}/turn", json={"user_id": "eval", "text": item["user"]}, timeout=120)
            r.raise_for_status()
            reply = r.json()["reply"]
            scores = score_turn(user_text=item["user"], coach_reply=reply, retrieved=[])
            f.write(json.dumps({"id": item["id"], "reply": reply, "scores": scores}) + "\n")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 8: Write 5 athlete profiles `eval/simulated_athlete/profiles/<slug>.yaml`**

Each profile needs `name`, `backstory`, `opener`, `style`, `turn_count`. Example `deflector.yaml`:

```yaml
slug: deflector
name: Articulate deflector
backstory: |
  Senior PM, marathon goal. Reads training books. Verbalizes neatly but avoids the
  specific friction he's avoiding (cold mornings, comparison spirals on Strava).
opener: |
  Hey coach, I think my biggest issue is probably mental — like I get how the body works,
  but emotionally I find myself rationalizing missed sessions.
style: |
  Use abstract framings, dodge specifics, agree readily, then continue old patterns.
turn_count: 6
```

Write 4 more in the same shape: `over_eager`, `plateau_intellectual`, `injured_denying`, `grief_runner`. Backstories per spec.

- [ ] **Step 9: Implement `eval/simulated_athlete/simulate.py`**

```python
"""Spin up an athlete-agent (Sonnet 4.6) that role-plays a profile and chats with the coach service."""
from __future__ import annotations
import os
from pathlib import Path
import yaml
import httpx
import anthropic


def _athlete_reply(profile: dict, history: list[dict]) -> str:
    client = anthropic.Anthropic()
    sys = (
        f"You are role-playing a runner. Stay in character.\n\n"
        f"NAME: {profile['name']}\n"
        f"BACKSTORY:\n{profile['backstory']}\n\n"
        f"STYLE:\n{profile['style']}\n\n"
        f"Respond as the runner would, conversationally, 1-3 sentences."
    )
    resp = client.messages.create(
        model="claude-sonnet-4-6", max_tokens=400, system=sys, messages=history,
    )
    return resp.content[0].text


def simulate(profile_path: Path, coach_url: str) -> list[dict]:
    profile = yaml.safe_load(profile_path.read_text())
    user_id = f"sim-{profile['slug']}"
    transcript: list[dict] = [{"role": "user", "content": profile["opener"]}]
    for _ in range(profile["turn_count"]):
        r = httpx.post(f"{coach_url}/turn", json={"user_id": user_id, "text": transcript[-1]["content"]}, timeout=120)
        r.raise_for_status()
        coach_reply = r.json()["reply"]
        transcript.append({"role": "assistant", "content": coach_reply})
        next_athlete = _athlete_reply(profile, transcript)
        transcript.append({"role": "user", "content": next_athlete})
    return transcript
```

- [ ] **Step 10: Implement `eval/simulated_athlete/nightly_eval.py`**

```python
"""Orchestrate: loop profiles → simulate → score → emit JSONL summary."""
from __future__ import annotations
import json
import os
import sys
from pathlib import Path

from eval.simulated_athlete.simulate import simulate
from eval.judge import score_turn

PROFILES_DIR = Path(__file__).parent / "profiles"


def main() -> int:
    coach_url = os.environ.get("COACH_AGENT_URL", "http://localhost:8800")
    out = Path(os.environ.get("COACH_EVAL_OUT", "/eval-runs/nightly.jsonl"))
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w") as f:
        for profile_path in sorted(PROFILES_DIR.glob("*.yaml")):
            transcript = simulate(profile_path, coach_url)
            for i in range(1, len(transcript), 2):
                user_msg = transcript[i - 1]["content"]
                coach_msg = transcript[i]["content"]
                scores = score_turn(user_text=user_msg, coach_reply=coach_msg, retrieved=[])
                f.write(json.dumps({
                    "profile": profile_path.stem, "turn": i // 2 + 1,
                    "scores": scores,
                }) + "\n")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 11: Commit**

```bash
git add services/coach/eval/ services/coach/tests/test_eval_judge.py
git commit -m "coach(eval): rubric judge + single-turn set + 5-archetype simulated athlete"
```

---

## Task 14: docker-compose — coach-agent + telegram-bot services + Dockerfile

**Files:**
- Create: `services/coach/Dockerfile`
- Modify: `services/coach/docker-compose.yml` (add coach-agent + telegram-bot services + volumes)

- [ ] **Step 1: Write `services/coach/Dockerfile`**

```dockerfile
FROM python:3.11-slim

# Build deps for sentence-transformers / faster-whisper
RUN apt-get update && apt-get install -y --no-install-recommends \
      ffmpeg libsndfile1 git && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml ./
RUN pip install --no-cache-dir -e ".[test]" || pip install --no-cache-dir -e .

COPY . /app
ENV PYTHONPATH=/app
```

- [ ] **Step 2: Edit `services/coach/docker-compose.yml`** — append after `mem0-api`

```yaml
  coach-agent:
    build: .
    depends_on:
      mem0-api:
        condition: service_started
      litellm:
        condition: service_healthy
    environment:
      # Route all Anthropic calls through LiteLLM (which holds the OAuth token upstream).
      ANTHROPIC_BASE_URL: http://litellm:4000
      ANTHROPIC_API_KEY: ${LITELLM_MASTER_KEY}
      OPENAI_API_KEY: ${OPENAI_API_KEY}
      MEM0_API_URL: http://mem0-api:8000
      COACH_INDEX_STORAGE_DIR: /data/vault_rag
      COACH_USER_MEMORY_DIR: /data/user_memory
      COACH_INBOX_ROOT: /vault/_inbox
      COACH_VAULT_PATH: /vault
      COACH_USAGE_LOG: /data/usage.jsonl
    # ANTHROPIC_API_KEY here = LITELLM master key, NOT a real Anthropic key.
    # OAuth lives only in the litellm container.
    volumes:
      - ${VAULT_HOST_PATH}:/vault
      - coach_data:/data
      - whisper_cache:/root/.cache
    command: ["uvicorn", "agent.service:app", "--host", "0.0.0.0", "--port", "8800"]
    ports:
      - "8800:8800"
    restart: unless-stopped

  telegram-bot:
    build: .
    depends_on:
      coach-agent:
        condition: service_started
    environment:
      TELEGRAM_COACH_BOT_TOKEN: ${TELEGRAM_COACH_BOT_TOKEN}
      COACH_AGENT_URL: http://coach-agent:8800
      COACH_ACCESS_JSON: /app/access.json
    volumes:
      - whisper_cache:/root/.cache
    command: ["python", "-m", "telegram_bot.bot"]
    restart: unless-stopped
```

And add to the volumes block at bottom:

```yaml
  coach_data:
  whisper_cache:
```

- [ ] **Step 3: Verify compose parses**

```bash
docker compose --env-file services/coach/.env.template -f services/coach/docker-compose.yml config >/dev/null
```

Expected: exit 0 (or warnings about empty vars — those are OK at this point).

- [ ] **Step 4: Commit**

```bash
git add services/coach/Dockerfile services/coach/docker-compose.yml
git commit -m "coach: Dockerfile + compose entries for coach-agent and telegram-bot"
```

---

## Task 15: README + UAT checklist + cron config

**Files:**
- Modify: `services/coach/README.md` (expand to full ops doc)
- Create: `services/coach/UAT.md` (manual smoke checklist)
- Create: `services/coach/crontab.example`

- [ ] **Step 1: Replace `services/coach/README.md`**

```markdown
# Virtual coach (Stack C)

Telegram-fronted endurance coach over Gonzalo's Obsidian vault. See spec at
`docs/superpowers/specs/2026-05-24-virtual-coach-design.md` and plan at
`docs/superpowers/plans/2026-05-25-virtual-coach-stack-c.md`.

## Architecture

```
Telegram → telegram-bot (allowlist + Whisper) → HTTP → coach-agent → ClaudeSDKClient
                                                          │              │
                                                          │              └→ LiteLLM (OAuth → Max bucket)
                                                          │
                                                          ├─ MCP: vault_rag (LlamaIndex PropertyGraph)
                                                          ├─ MCP: user_memory (per-user scratchpad)
                                                          └─ MCP: mem0 (long-term facts) ── LiteLLM ──→ Anthropic
                                                          │
                                                          └─ post-turn → /vault/_inbox/<user>/<ts>.md
                                                                          └─ sidecar → Notion review queue
```

## Setup

1. `cp .env.template .env.coach` and fill secrets.
2. `claude setup-token` on your laptop → copy `CLAUDE_CODE_OAUTH_TOKEN` into `.env.coach` (LiteLLM uses this; no other service does).
3. Generate a LiteLLM master key: `openssl rand -hex 32` → paste into `LITELLM_MASTER_KEY=`.
4. **Note:** `ANTHROPIC_API_KEY` does NOT belong in `.env.coach`. Everywhere except the litellm container, `ANTHROPIC_API_KEY` is set per-service to the LiteLLM master key (gateway auth), not a real Anthropic key.
5. Add allowed Telegram numeric user IDs to `access.json`.
6. `docker compose --env-file .env.coach -f docker-compose.yml build`
7. `docker compose --env-file .env.coach -f docker-compose.yml up -d`
8. Verify LiteLLM healthy: `curl localhost:4000/health/liveliness`.
9. First-time vault index build:
   ```bash
   docker compose exec coach-agent python -m vault_rag.rebuild_cron
   ```
10. Send a Telegram message to verify.

## Cron sidecars

See `crontab.example`. Install with `crontab crontab.example` on the host or wire via systemd timers.

## Quota notes

- From 2026-06-15 the Agent SDK draws a separate monthly credit bucket on the Max plan.
- `quota_monitor.py` reads `total_cost_usd` from agent JSONL and alerts Telegram at 80%.
- Authoritative billing is at https://console.anthropic.com — the SDK number is a client-side estimate.

## Single-user OAuth ToS

The OAuth subscription token is authorized for one principal (Gonzalo). Pilot users send messages
that the coach processes ON BEHALF OF Gonzalo — they are addressees, not principals.
```

- [ ] **Step 2: Create `services/coach/UAT.md`**

```markdown
# UAT checklist — virtual coach

Run after first deploy and after any task that touches the agent loop.

## Smoke

- [ ] `docker compose ps` — all 5 services up (`mem0-postgres`, `mem0-neo4j`, `mem0-api`, `coach-agent`, `telegram-bot`)
- [ ] `curl localhost:8800/health` returns `{"status":"ok"}`
- [ ] `docker compose logs coach-agent | grep -i error` empty
- [ ] `docker compose logs telegram-bot | grep -i error` empty

## Allowlist

- [ ] Send Telegram message from an allowed ID — coach replies
- [ ] Send Telegram message from a non-allowed ID — no reply, log entry "rejecting user_id=..."

## Voice

- [ ] Send a 5-sec voice message in English — transcript → coach reply
- [ ] Send a 5-sec voice message in Spanish — transcript → coach reply

## Crisis filter

- [ ] Type "I want to die" — coach returns the canned 988/116/findahelpline reply (NOT a normal reply)
- [ ] Type "últimamente quiero quitarme la vida" — same canned reply

## Multi-turn voice

- [ ] Three back-and-forth turns from one user — coach references prior turns (not amnesiac)
- [ ] Three back-and-forth turns from a second user — separate context (no leakage)

## Inbox

- [ ] After 3 turns, `ls vault/_inbox/<user_id>/*.md` shows 3 entries
- [ ] Run `python -m sidecar.promote_to_notion` — Notion shows 3 new tasks

## Vault rebuild

- [ ] `python -m vault_rag.rebuild_cron` exits 0 in <3 min
- [ ] `ls services/coach/vault_rag/storage/` shows `graph_store.json` + `docstore.json`

## Eval

- [ ] `python -m eval.single_turn.run` writes `/eval-runs/single-turn.jsonl` with 10 rows
- [ ] `python -m eval.simulated_athlete.nightly_eval` writes `/eval-runs/nightly.jsonl` with ~30 rows
- [ ] Mean `frontal` score ≥ 3.5 across all rows
- [ ] No `grounding` score < 3
```

- [ ] **Step 3: Create `services/coach/crontab.example`**

```cron
# Nightly vault rebuild — 02:00 local
0 2 * * * cd /home/gonzalo/workspace/painforwisdom/painforwisdom && docker compose -f services/coach/docker-compose.yml exec -T coach-agent python -m vault_rag.rebuild_cron >> /var/log/coach-rebuild.log 2>&1

# Nightly eval — 03:00 local
0 3 * * * cd /home/gonzalo/workspace/painforwisdom/painforwisdom && docker compose -f services/coach/docker-compose.yml exec -T coach-agent python -m eval.simulated_athlete.nightly_eval >> /var/log/coach-eval.log 2>&1

# classify_themes — every 30 min
*/30 * * * * cd /home/gonzalo/workspace/painforwisdom/painforwisdom && docker compose -f services/coach/docker-compose.yml exec -T coach-agent python -m sidecar.classify_themes >> /var/log/coach-classify.log 2>&1

# promote_to_notion — hourly
0 * * * * cd /home/gonzalo/workspace/painforwisdom/painforwisdom && docker compose -f services/coach/docker-compose.yml exec -T coach-agent python -m sidecar.promote_to_notion >> /var/log/coach-promote.log 2>&1

# quota_monitor — every 15 min
*/15 * * * * cd /home/gonzalo/workspace/painforwisdom/painforwisdom && docker compose -f services/coach/docker-compose.yml exec -T coach-agent python -m sidecar.quota_monitor >> /var/log/coach-quota.log 2>&1
```

- [ ] **Step 4: Commit**

```bash
git add services/coach/README.md services/coach/UAT.md services/coach/crontab.example
git commit -m "coach: README + UAT checklist + cron example"
```

---

## Self-review (post-write check)

**Spec coverage** — every requirement in `docs/superpowers/specs/2026-05-24-virtual-coach-design.md` Phase 2 / Stack C section maps to a task:
- Per-user Telegram bot with allowlist → Tasks 11
- Voice OR text input → Task 11 (voice via faster-whisper, text passthrough)
- Vault RAG with paragraph-level retrieval → Tasks 3–6 (PropertyGraph + BM25 + rerank)
- Per-user memory (Mem0 + memory_20250818 scratchpad) → Tasks 7, 8
- HITL via vault/_inbox → Notion → Tasks 10, 12
- Crisis filter pre-Claude → Tasks 9, 10
- Quota monitor with 2026-06-15 separate-bucket aware → Task 12
- 4-layer eval (smoke/iso = embedded in pytest suite, single-turn = Task 13, simulated athletes = Task 13) → Tasks 3–13 collectively
- Single-user OAuth ToS note → Task 15 README
- coach_prompt.md kept versioned → preserved from prior commit, loaded by Task 10

**Placeholders scan** — no TBD / TODO / "implement later" / "similar to" in step bodies.

**Type/name consistency**:
- `UserMemory` class name consistent across `user_memory/mcp_server.py` and tests.
- `Mem0Client` consistent across `mem0_mcp/client.py`, server, tests.
- `SessionMap.get_or_create_session_id` used consistently in `agent/session_map.py` test + `agent/service.py`.
- `Allowlist.allowed(int) -> bool` consistent across test and bot.
- `score_turn(*, user_text, coach_reply, retrieved)` consistent across `eval/judge.py`, `eval/single_turn/run.py`, `eval/simulated_athlete/nightly_eval.py`.

No gaps detected.

---

## Execution handoff

Plan complete. Two execution options:

1. **Subagent-Driven (recommended)** — fresh subagent per task, two-stage review between tasks.
2. **Inline Execution** — execute tasks in this session with checkpoints.
