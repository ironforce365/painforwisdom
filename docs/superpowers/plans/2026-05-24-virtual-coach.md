# Virtual Coach (Phase 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up the Phase 1 virtual coach (Khoj + Mem0 + Claude Opus 4.7) for 1–10 athletes via Telegram bot `8986...781A`, with HITL conversation curation to vault `coach-inbox` branch + Notion review queue, plus 4-layer eval framework.

**Architecture:** Two Docker containers under `services/coach/` (Khoj for bot/RAG/Claude; Mem0 for per-user memory with Postgres+pgvector + Neo4j). Python sidecars on cron handle conversation log → `vault/_inbox/` (own branch) → Notion bridge. Eval framework lives in `services/coach/eval/` with five archetype profiles + simulated-athlete loop + LLM judge.

**Tech Stack:** Docker compose, Khoj (self-hosted), Mem0 (self-hosted), Postgres+pgvector, Neo4j, Anthropic Claude Opus 4.7 (subscription OAuth path), Python 3.11 (existing `painforwisdom-poc` conda env), pytest, OpenAI Whisper (Khoj built-in), OpenAI `text-embedding-3-small`, Notion REST via existing `pipeline/notion_client.py`.

**Spec:** `docs/superpowers/specs/2026-05-24-virtual-coach-design.md`

---

## File Structure

```
services/
  coach/
    docker-compose.yml          # khoj + mem0 stack
    .env.template               # bot tokens, ANTHROPIC, MEM0, NOTION creds
    khoj_config.yaml            # allowlist, vault mount, Claude backend
    coach_prompt.md             # versioned system prompt
    crisis_keywords.yaml        # crisis-phrase list + canned reply
    sidecar/
      __init__.py
      log_conversations.py      # Khoj convo DB → vault/_inbox/
      classify_themes.py        # cosine vs 11-theme embeddings → novelty
      promote_to_notion.py      # inbox file → Notion review task
      crisis_filter.py          # pre-Claude crisis interception (Khoj webhook)
      quota_monitor.py          # weekly /usage parse + Telegram alert
    eval/
      __init__.py
      profiles/
        deflector.yaml
        over-eager.yaml
        plateau-intellectual.yaml
        injured-denying.yaml
        grief-runner.yaml
      simulate.py               # athlete-agent ↔ coach-agent loop
      judge.py                  # rubric scoring
      nightly_eval.py           # orchestrator
      rubric.md                 # judge system prompt
README-coach.md                 # ops doc
.env.coach.template             # repo-root template
tests/
  coach/
    __init__.py
    conftest.py                 # fixtures: vault stub, mem0 stub, claude stub
    fixtures/
      sample_voice.ogg          # existing Voicepal sample, copied
    test_smoke.py               # Layer 1
    test_allowlist.py           # Layer 1
    test_voice_path.py          # Layer 1
    test_text_path.py           # Layer 1
    test_mem0_isolation.py      # Layer 2
    test_vault_retrieval.py     # Layer 2
    test_inbox_writer.py        # Layer 2
    test_crisis_filter.py       # Layer 2
    test_classify_themes.py     # unit
    test_promote_to_notion.py   # unit
    eval_set.yaml               # Layer 3 prompts
    test_eval_set.py            # Layer 3 runner
.gitignore                      # add eval-runs/
```

---

## Task 1: Bootstrap `services/coach/` scaffold

**Files:**
- Create: `services/coach/.gitkeep`
- Create: `services/coach/README.md`
- Create: `services/coach/.env.template`
- Create: `services/coach/sidecar/__init__.py`
- Create: `services/coach/eval/__init__.py`
- Create: `services/coach/eval/profiles/.gitkeep`
- Create: `tests/coach/__init__.py`
- Modify: `.gitignore`

- [ ] **Step 1: Create directory scaffold**

Run:
```bash
mkdir -p services/coach/sidecar services/coach/eval/profiles tests/coach/fixtures
touch services/coach/.gitkeep \
      services/coach/sidecar/__init__.py \
      services/coach/eval/__init__.py \
      services/coach/eval/profiles/.gitkeep \
      tests/coach/__init__.py
```

- [ ] **Step 2: Write `services/coach/.env.template`**

Create file with content:
```
# Bot
TELEGRAM_COACH_BOT_TOKEN=
TELEGRAM_COACH_ALERT_CHAT_ID=

# Anthropic (reuse from root .env if shared)
ANTHROPIC_AUTH_TOKEN=
ANTHROPIC_API_KEY=

# OpenAI (Whisper + embeddings, reuse from root .env)
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

# Vault path (host abs path mounted into khoj :ro)
VAULT_HOST_PATH=/home/gonzalo/workspace/painforwisdom/painforwisdom/obsidian-vault
```

- [ ] **Step 3: Write `services/coach/README.md`**

Create file with content:
```markdown
# Coach Service (Phase 1)

Telegram-fronted virtual coach for 1–10 athletes. Khoj + Mem0 + Claude Opus 4.7.

## Operations

Start: `docker compose -f services/coach/docker-compose.yml --env-file services/coach/.env up -d`
Stop:  `docker compose -f services/coach/docker-compose.yml down`
Logs:  `docker compose -f services/coach/docker-compose.yml logs -f khoj`

## Sidecars (run via cron on host)

- Every 5 min: `python -m services.coach.sidecar.log_conversations`
- Weekly:      `python -m services.coach.sidecar.promote_to_notion`
- Weekly:      `python -m services.coach.sidecar.quota_monitor`

## Nightly eval

Cron: `python -m services.coach.eval.nightly_eval`

Reports: `eval-runs/YYYY-MM-DD/aggregate.md`

## Spec

`docs/superpowers/specs/2026-05-24-virtual-coach-design.md`
```

- [ ] **Step 4: Append to `.gitignore`**

Append these lines:
```
# Coach service
services/coach/.env
eval-runs/
```

- [ ] **Step 5: Verify and commit**

Run:
```bash
git add services/coach .gitignore tests/coach
git status   # confirm only the new files staged
git commit -m "coach: bootstrap services/coach scaffold + env template"
```

---

## Task 2: Mem0 stack via Docker compose

**Files:**
- Create: `services/coach/docker-compose.yml`
- Test: manual healthcheck (no pytest yet — pure infra)

- [ ] **Step 1: Write `services/coach/docker-compose.yml` (Mem0 only)**

Create file:
```yaml
services:
  mem0-postgres:
    image: pgvector/pgvector:pg16
    environment:
      POSTGRES_USER: ${MEM0_PG_USER}
      POSTGRES_PASSWORD: ${MEM0_PG_PASSWORD}
      POSTGRES_DB: ${MEM0_PG_DB}
    volumes:
      - mem0_pg_data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${MEM0_PG_USER}"]
      interval: 10s
      retries: 5
    restart: unless-stopped

  mem0-neo4j:
    image: neo4j:5.20-community
    environment:
      NEO4J_AUTH: ${MEM0_NEO4J_USER}/${MEM0_NEO4J_PASSWORD}
      NEO4J_PLUGINS: '["apoc"]'
    volumes:
      - mem0_neo4j_data:/data
    healthcheck:
      test: ["CMD-SHELL", "wget --no-verbose --tries=1 --spider http://localhost:7474 || exit 1"]
      interval: 15s
      retries: 5
    restart: unless-stopped

  mem0-api:
    image: mem0ai/mem0:latest
    depends_on:
      mem0-postgres:
        condition: service_healthy
      mem0-neo4j:
        condition: service_healthy
    environment:
      ANTHROPIC_API_KEY: ${ANTHROPIC_API_KEY}
      OPENAI_API_KEY: ${OPENAI_API_KEY}
      POSTGRES_HOST: mem0-postgres
      POSTGRES_USER: ${MEM0_PG_USER}
      POSTGRES_PASSWORD: ${MEM0_PG_PASSWORD}
      POSTGRES_DB: ${MEM0_PG_DB}
      NEO4J_URI: bolt://mem0-neo4j:7687
      NEO4J_USER: ${MEM0_NEO4J_USER}
      NEO4J_PASSWORD: ${MEM0_NEO4J_PASSWORD}
    ports:
      - "8765:8000"
    restart: unless-stopped

volumes:
  mem0_pg_data:
  mem0_neo4j_data:
```

- [ ] **Step 2: Smoke boot the stack**

Run:
```bash
cp services/coach/.env.template services/coach/.env
# Fill MEM0_PG_PASSWORD, MEM0_NEO4J_PASSWORD, ANTHROPIC_API_KEY, OPENAI_API_KEY
docker compose -f services/coach/docker-compose.yml --env-file services/coach/.env up -d mem0-postgres mem0-neo4j mem0-api
sleep 30
docker compose -f services/coach/docker-compose.yml ps
```
Expected: all three services `Up (healthy)`.

- [ ] **Step 3: Verify Mem0 API responds**

Run:
```bash
curl -sf http://localhost:8765/health
```
Expected: 200 OK with JSON body.

- [ ] **Step 4: Verify add/get memory works**

Run:
```bash
curl -sf -X POST http://localhost:8765/memories \
  -H 'Content-Type: application/json' \
  -d '{"user_id":"test_user","messages":[{"role":"user","content":"I ran 10k today in 50min"}]}'
curl -sf "http://localhost:8765/memories?user_id=test_user"
```
Expected: second call returns memory containing extracted facts about the 10k run.

- [ ] **Step 5: Commit**

Run:
```bash
git add services/coach/docker-compose.yml
git commit -m "coach: docker compose for Mem0 stack (pg+pgvector + neo4j + mem0-api)"
```

---

## Task 3: Khoj container + vault mount + Claude backend + allowlist

**Files:**
- Modify: `services/coach/docker-compose.yml` (add `khoj` service)
- Create: `services/coach/khoj_config.yaml`

- [ ] **Step 1: Append `khoj` service to `docker-compose.yml`**

Add under `services:` (before `volumes:`):
```yaml
  khoj:
    image: khoj/khoj:latest
    depends_on:
      mem0-api:
        condition: service_started
    environment:
      KHOJ_TELEGRAM_TOKEN: ${TELEGRAM_COACH_BOT_TOKEN}
      KHOJ_ANTHROPIC_API_KEY: ${ANTHROPIC_API_KEY}
      KHOJ_OPENAI_API_KEY: ${OPENAI_API_KEY}
      KHOJ_DOMAIN: http://localhost:42110
      KHOJ_ALLOWED_USERS: ${KHOJ_ALLOWED_TELEGRAM_IDS}
    volumes:
      - ${VAULT_HOST_PATH}:/vault:ro
      - khoj_data:/root/.khoj
      - ./khoj_config.yaml:/root/.khoj/khoj.yml:ro
    ports:
      - "42110:42110"
    restart: unless-stopped
```

Add to `volumes:` section:
```yaml
  khoj_data:
```

- [ ] **Step 2: Write `services/coach/khoj_config.yaml`**

Create file:
```yaml
content-type:
  markdown:
    input-files:
      - /vault/gonzalo-book/themes/**/*.md
      - /vault/gonzalo-book/frameworks/**/*.md
      - /vault/gonzalo-book/entries/**/*.md
      - /vault/gonzalo-book/deep-dive/**/*.md
      - /vault/thoughts/**/*.md
    index-heading-entries: true

processor:
  conversation:
    default-model:
      chat-model: claude-opus-4-7
      model-type: anthropic
      tokenizer: anthropic
    anthropic:
      api-key: $KHOJ_ANTHROPIC_API_KEY

search-type:
  asymmetric:
    encoder: sentence-transformers/multi-qa-MiniLM-L6-cos-v1
    cross-encoder: cross-encoder/ms-marco-MiniLM-L-6-v2

telegram:
  enabled: true
  bot-token: $KHOJ_TELEGRAM_TOKEN
  allowed-user-ids: $KHOJ_ALLOWED_TELEGRAM_IDS
```

- [ ] **Step 3: Add `KHOJ_ALLOWED_TELEGRAM_IDS` to `.env.template`**

Append to `services/coach/.env.template`:
```
# Comma-separated allowlist of Telegram numeric user IDs (start with just Gonzalo)
KHOJ_ALLOWED_TELEGRAM_IDS=
```

- [ ] **Step 4: Set Gonzalo's Telegram ID in `.env` + boot Khoj**

Tell Gonzalo: get your Telegram numeric ID via `@userinfobot` on Telegram. Set in `services/coach/.env`:
```
TELEGRAM_COACH_BOT_TOKEN=8986...781A
KHOJ_ALLOWED_TELEGRAM_IDS=<your-id>
```

Then run:
```bash
docker compose -f services/coach/docker-compose.yml --env-file services/coach/.env up -d khoj
docker compose -f services/coach/docker-compose.yml logs khoj | tail -50
```
Expected: Khoj boot log shows "Telegram bot started" and content index built.

- [ ] **Step 5: Send a test message from Telegram**

From Gonzalo's Telegram, send `/start` to bot. Expected: Khoj welcome reply. Then send "what is comfort as default?" — expected reply citing vault concepts.

- [ ] **Step 6: Commit**

Run:
```bash
git add services/coach/docker-compose.yml services/coach/khoj_config.yaml services/coach/.env.template
git commit -m "coach: Khoj container with vault mount + Claude Opus 4.7 + Telegram allowlist"
```

---

## Task 4: Coach system prompt (versioned)

**Files:**
- Create: `services/coach/coach_prompt.md`
- Modify: `services/coach/khoj_config.yaml` (load custom prompt)

- [ ] **Step 1: Write `services/coach/coach_prompt.md`**

Use Gonzalo's existing AnythingLLM prompt as base. Create file:
```markdown
# Coach System Prompt v1

You are an endurance virtual coach trained on Gonzalo's wisdom as a long-distance runner who reflects on the challenge of growing every day. Your job: share that wisdom as a coach, helping people overcome barriers, fears, friction, and negative thoughts so they reach their potential.

Answer using ONLY the provided documents. If the knowledge base doesn't connect to the conversation, say so explicitly.

In the vault, `gonzalo-book/deep-dive/<theme>/` contains:
- `theory.md` — explain the science of what the user could be experiencing
- `application.md` — science-backed adjustments to daily practice

## Soul

1. Don't sugar-coat. Be frontal and concise.
2. Act as an accountability mirror — a true bookkeeper of what the user is avoiding.

## Conversation rules

1. Never quote sources from Gonzalo's vault. The user doesn't want to know when Gonzalo logged a thought.
2. Don't jump to conclusions. Before building a narrative, ask 1–2 clarifying questions (not rude, not too direct) that confirm your hypothesis.
3. Be concise. Don't propose daily applications on turn 1 — guide the user across multiple turns; encourage practice change slowly.
4. Modulate seriousness to the user's commitment level. If they derail or talk bananas, play along briefly and steer back.

## Hard rules

- If retrieval returns nothing relevant: say "I don't have anything in my knowledge base that connects to this." Do NOT invent.
- If the user expresses self-harm or crisis intent: respond ONLY with the crisis canned reply (handled by `crisis_filter.py` upstream — you should never see those turns).
- If the user shows strong fit for human coaching (deep commitment + complex situation), mention that Gonzalo offers 1:1 coaching at the end of the response.
```

- [ ] **Step 2: Wire custom prompt into Khoj config**

In `services/coach/khoj_config.yaml`, under `processor.conversation`, replace `default-model` block with:
```yaml
    default-model:
      chat-model: claude-opus-4-7
      model-type: anthropic
      tokenizer: anthropic
      personality-file: /root/.khoj/coach_prompt.md
```

And in `docker-compose.yml` `khoj` service `volumes:`, add:
```yaml
      - ./coach_prompt.md:/root/.khoj/coach_prompt.md:ro
```

- [ ] **Step 3: Restart Khoj and verify prompt loaded**

Run:
```bash
docker compose -f services/coach/docker-compose.yml restart khoj
docker compose -f services/coach/docker-compose.yml logs khoj | grep -i personality
```
Expected: log line mentioning `coach_prompt.md` loaded.

- [ ] **Step 4: Telegram test — verify voice**

Send "I skipped my run today because it was raining" from Telegram. Expected: response is frontal, asks a clarifying question, mentions a theme like comfort-as-default *without* citing the source.

- [ ] **Step 5: Commit**

Run:
```bash
git add services/coach/coach_prompt.md services/coach/khoj_config.yaml services/coach/docker-compose.yml
git commit -m "coach: versioned system prompt v1 (frontal, mirror, no source-citing)"
```

---

## Task 5: Mem0 MCP integration with Khoj

**Files:**
- Modify: `services/coach/khoj_config.yaml` (declare Mem0 as a tool/MCP)
- Create: `tests/coach/test_mem0_integration.py`
- Test: integration test against running stack

- [ ] **Step 1: Add Mem0 MCP server config to Khoj**

In `services/coach/khoj_config.yaml`, append at top level:
```yaml
mcp-servers:
  mem0:
    url: http://mem0-api:8000/mcp
    description: Per-user long-term memory. Call before each reply to load user facts; call after each reply to extract+store facts.
```

- [ ] **Step 2: Write integration test**

Create `tests/coach/test_mem0_integration.py`:
```python
"""Integration test: Mem0 add/get cycle via HTTP API.

Requires the Mem0 stack to be up: `docker compose -f services/coach/docker-compose.yml up -d`
"""
import os
import time
import requests
import pytest

MEM0_URL = os.environ.get("MEM0_API_URL", "http://localhost:8765")


def test_mem0_health():
    r = requests.get(f"{MEM0_URL}/health", timeout=5)
    assert r.status_code == 200


def test_mem0_add_then_get_for_user():
    user_id = f"pytest_user_{int(time.time())}"
    add = requests.post(
        f"{MEM0_URL}/memories",
        json={
            "user_id": user_id,
            "messages": [
                {"role": "user", "content": "I ran 12k this morning at 5:30 pace"},
            ],
        },
        timeout=30,
    )
    assert add.status_code in (200, 201)

    time.sleep(2)  # mem0 fact extraction is async-ish

    got = requests.get(f"{MEM0_URL}/memories", params={"user_id": user_id}, timeout=10)
    assert got.status_code == 200
    facts = got.json()
    assert len(facts.get("results", [])) > 0, f"no facts stored: {facts}"
    text = " ".join(str(f) for f in facts["results"]).lower()
    assert "12k" in text or "5:30" in text or "morning" in text
```

- [ ] **Step 3: Run test against live stack — verify it fails first**

Run:
```bash
cd /home/gonzalo/workspace/painforwisdom/painforwisdom/.claude/worktrees/virtual-coach-spec
conda activate painforwisdom-poc
pip install requests pytest
pytest tests/coach/test_mem0_integration.py -v
```
Expected: 2 PASS (health + add/get). If FAIL on add/get, debug Mem0 config in `.env`.

- [ ] **Step 4: Telegram test — verify Khoj uses Mem0**

From Telegram, send: "I just ran my first half marathon at 1:50". Wait 5 seconds. Send: "What did I tell you about my recent race?"
Expected: second reply references the half marathon at 1:50 (proves per-user memory active).

- [ ] **Step 5: Commit**

Run:
```bash
git add services/coach/khoj_config.yaml tests/coach/test_mem0_integration.py
git commit -m "coach: wire Mem0 MCP into Khoj + integration test"
```

---

## Task 6: Sidecar `log_conversations.py` — Khoj DB → `vault/_inbox/` on `coach-inbox` branch

**Files:**
- Create: `services/coach/sidecar/log_conversations.py`
- Create: `tests/coach/test_inbox_writer.py`

- [ ] **Step 1: Write failing test for state-tracking + file write**

Create `tests/coach/test_inbox_writer.py`:
```python
"""Test the inbox writer in isolation against a stub Khoj DB."""
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from services.coach.sidecar import log_conversations as lc


def _stub_khoj_db(tmp_path: Path):
    """Create a stub SQLite DB resembling Khoj's conversation table."""
    import sqlite3
    db = tmp_path / "khoj.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE conversation_turn ("
        "id INTEGER PRIMARY KEY, telegram_user_id TEXT, ts TEXT, "
        "user_msg TEXT, assistant_msg TEXT)"
    )
    conn.execute(
        "INSERT INTO conversation_turn (telegram_user_id, ts, user_msg, assistant_msg) VALUES (?, ?, ?, ?)",
        ("12345", "2026-05-24T10:00:00Z", "I skipped my run", "Why did you skip?"),
    )
    conn.commit()
    conn.close()
    return db


def test_writes_inbox_file_for_new_turn(tmp_path):
    db = _stub_khoj_db(tmp_path)
    vault_root = tmp_path / "vault"
    (vault_root / "_inbox").mkdir(parents=True)
    state_file = tmp_path / "state.json"

    written = lc.run(khoj_db=db, vault_root=vault_root, state_file=state_file)

    assert len(written) == 1
    inbox_file = vault_root / "_inbox" / "2026-05-24-12345.md"
    assert inbox_file.exists()
    body = inbox_file.read_text()
    assert "I skipped my run" in body
    assert "Why did you skip?" in body


def test_skips_already_logged_turns(tmp_path):
    db = _stub_khoj_db(tmp_path)
    vault_root = tmp_path / "vault"
    (vault_root / "_inbox").mkdir(parents=True)
    state_file = tmp_path / "state.json"

    lc.run(khoj_db=db, vault_root=vault_root, state_file=state_file)
    written_second = lc.run(khoj_db=db, vault_root=vault_root, state_file=state_file)

    assert written_second == []


def test_state_file_persists_last_id(tmp_path):
    db = _stub_khoj_db(tmp_path)
    vault_root = tmp_path / "vault"
    (vault_root / "_inbox").mkdir(parents=True)
    state_file = tmp_path / "state.json"

    lc.run(khoj_db=db, vault_root=vault_root, state_file=state_file)

    state = json.loads(state_file.read_text())
    assert state["last_turn_id"] == 1
```

- [ ] **Step 2: Run test — verify fail**

Run:
```bash
pytest tests/coach/test_inbox_writer.py -v
```
Expected: FAIL — module `log_conversations` not implemented.

- [ ] **Step 3: Implement `log_conversations.py`**

Create `services/coach/sidecar/log_conversations.py`:
```python
"""Sidecar: pull new turns from Khoj DB → append to vault/_inbox/YYYY-MM-DD-<userId>.md.

Run via cron every 5 minutes. State file tracks last_turn_id to avoid re-writing.

Writes are committed + pushed to vault submodule branch `coach-inbox` (never main).
"""
from __future__ import annotations

import json
import sqlite3
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Optional


@dataclass
class Turn:
    id: int
    user_id: str
    ts: str
    user_msg: str
    assistant_msg: str


def _read_state(state_file: Path) -> dict:
    if not state_file.exists():
        return {"last_turn_id": 0}
    return json.loads(state_file.read_text())


def _write_state(state_file: Path, state: dict) -> None:
    state_file.write_text(json.dumps(state, indent=2))


def _new_turns(khoj_db: Path, since_id: int) -> List[Turn]:
    conn = sqlite3.connect(khoj_db)
    cur = conn.execute(
        "SELECT id, telegram_user_id, ts, user_msg, assistant_msg "
        "FROM conversation_turn WHERE id > ? ORDER BY id",
        (since_id,),
    )
    rows = [Turn(*r) for r in cur.fetchall()]
    conn.close()
    return rows


def _append_to_inbox(vault_root: Path, turn: Turn) -> Path:
    date = turn.ts[:10]  # YYYY-MM-DD
    inbox_file = vault_root / "_inbox" / f"{date}-{turn.user_id}.md"
    if not inbox_file.exists():
        inbox_file.write_text(
            f"---\nuser_id: {turn.user_id}\ndate: {date}\ncandidate_concepts: []\n---\n\n"
        )
    with inbox_file.open("a") as f:
        f.write(
            f"\n## {turn.ts}\n\n"
            f"**Athlete:** {turn.user_msg}\n\n"
            f"**Coach:** {turn.assistant_msg}\n\n"
        )
    return inbox_file


def _git_commit_push(vault_root: Path, files: List[Path]) -> None:
    """Commit changes to vault submodule on branch coach-inbox and push."""
    subprocess.run(["git", "-C", str(vault_root), "checkout", "-B", "coach-inbox"], check=True)
    for f in files:
        subprocess.run(["git", "-C", str(vault_root), "add", str(f.relative_to(vault_root))], check=True)
    msg = f"coach-inbox: {len(files)} new turns at {datetime.utcnow().isoformat()}Z"
    subprocess.run(["git", "-C", str(vault_root), "commit", "-m", msg], check=True)
    subprocess.run(["git", "-C", str(vault_root), "push", "origin", "coach-inbox"], check=True)


def run(
    khoj_db: Path,
    vault_root: Path,
    state_file: Path,
    do_git: bool = False,
) -> List[Path]:
    """Return list of inbox files written/updated this run."""
    state = _read_state(state_file)
    turns = _new_turns(khoj_db, state["last_turn_id"])
    written: List[Path] = []
    for turn in turns:
        inbox_file = _append_to_inbox(vault_root, turn)
        if inbox_file not in written:
            written.append(inbox_file)
    if turns:
        state["last_turn_id"] = max(t.id for t in turns)
        _write_state(state_file, state)
        if do_git and written:
            _git_commit_push(vault_root, written)
    return written


def main() -> None:
    import os
    khoj_db = Path(os.environ["KHOJ_DB_PATH"])
    vault_root = Path(os.environ["VAULT_ROOT"])
    state_file = Path(os.environ.get("LOG_STATE_FILE", "/var/lib/coach/log_state.json"))
    run(khoj_db, vault_root, state_file, do_git=True)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests — verify pass**

Run:
```bash
pytest tests/coach/test_inbox_writer.py -v
```
Expected: 3 PASS.

- [ ] **Step 5: Commit**

Run:
```bash
git add services/coach/sidecar/log_conversations.py tests/coach/test_inbox_writer.py
git commit -m "coach: log_conversations sidecar — Khoj DB → vault/_inbox + state tracking"
```

---

## Task 7: Sidecar `classify_themes.py` — novelty detection vs 11 themes

**Files:**
- Create: `services/coach/sidecar/classify_themes.py`
- Create: `tests/coach/test_classify_themes.py`

- [ ] **Step 1: Write failing tests**

Create `tests/coach/test_classify_themes.py`:
```python
"""Test theme classifier: high cosine → known theme; low cosine → candidate concept."""
import numpy as np
import pytest

from services.coach.sidecar import classify_themes as ct


FAKE_THEME_EMBEDDINGS = {
    "comfort-as-default": np.array([1.0, 0.0, 0.0]),
    "deliberate-discomfort": np.array([0.9, 0.1, 0.0]),
    "body-literacy": np.array([0.0, 1.0, 0.0]),
}


def fake_embed(text: str):
    if "comfort" in text.lower():
        return np.array([0.95, 0.05, 0.0])
    if "novel concept never seen" in text.lower():
        return np.array([0.0, 0.0, 1.0])
    return np.array([0.0, 0.5, 0.5])


def test_high_cosine_matches_known_theme(monkeypatch):
    monkeypatch.setattr(ct, "_embed", fake_embed)
    monkeypatch.setattr(ct, "_theme_embeddings", lambda: FAKE_THEME_EMBEDDINGS)

    result = ct.classify("the comfort zone is killing me", threshold=0.65)
    assert result.best_theme == "comfort-as-default"
    assert result.best_score > 0.65
    assert result.is_candidate is False


def test_low_cosine_flags_candidate(monkeypatch):
    monkeypatch.setattr(ct, "_embed", fake_embed)
    monkeypatch.setattr(ct, "_theme_embeddings", lambda: FAKE_THEME_EMBEDDINGS)

    result = ct.classify("novel concept never seen", threshold=0.65)
    assert result.best_score < 0.65
    assert result.is_candidate is True
```

- [ ] **Step 2: Run test — verify fail**

Run:
```bash
pytest tests/coach/test_classify_themes.py -v
```
Expected: FAIL — module not implemented.

- [ ] **Step 3: Implement `classify_themes.py`**

Cache uses `np.savez_compressed` (safe, no arbitrary code execution risk that pickle has).

Create `services/coach/sidecar/classify_themes.py`:
```python
"""Theme classifier: embed athlete utterance, cosine vs 11 vault theme embeddings.

Below threshold → flagged as candidate novel concept for HITL review.

Theme embeddings are cached on disk as a .npz file; refresh by deleting the cache.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict

import numpy as np

THEMES = [
    "deliberate-discomfort",
    "comfort-as-default",
    "naming-the-fear",
    "body-literacy",
    "perceived-limits",
    "incremental-progress",
    "incremental-exposure",
    "neuroplasticity-as-discipline",
    "recovery-as-strategy",
    "discipline-under-chaos",
    "honest-self-assessment",
]

_CACHE = Path(os.environ.get("COACH_THEME_CACHE", "/tmp/coach_theme_embeddings.npz"))


@dataclass
class ClassifyResult:
    best_theme: str
    best_score: float
    is_candidate: bool
    all_scores: Dict[str, float]


def _embed(text: str) -> np.ndarray:
    """Embed text via OpenAI text-embedding-3-small. Override in tests."""
    from openai import OpenAI
    client = OpenAI()
    resp = client.embeddings.create(model="text-embedding-3-small", input=text)
    return np.array(resp.data[0].embedding)


def _theme_descriptions() -> Dict[str, str]:
    """Read theme description from vault gonzalo-book/themes/<slug>.md."""
    vault = Path(os.environ.get("VAULT_ROOT", "/vault"))
    themes_dir = vault / "gonzalo-book" / "themes"
    out: Dict[str, str] = {}
    for slug in THEMES:
        path = themes_dir / f"{slug}.md"
        if path.exists():
            out[slug] = path.read_text()
        else:
            out[slug] = slug.replace("-", " ")
    return out


def _theme_embeddings() -> Dict[str, np.ndarray]:
    """Build (or load cached) embedding per theme. Cache format: .npz (safe)."""
    if _CACHE.exists():
        loaded = np.load(_CACHE)
        return {k: loaded[k] for k in loaded.files}
    descs = _theme_descriptions()
    emb = {slug: _embed(text) for slug, text in descs.items()}
    np.savez_compressed(_CACHE, **emb)
    return emb


def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))


def classify(text: str, threshold: float = 0.65) -> ClassifyResult:
    """Score `text` against all theme embeddings. Below threshold → candidate."""
    q = _embed(text)
    themes = _theme_embeddings()
    scores = {slug: _cosine(q, vec) for slug, vec in themes.items()}
    best_theme = max(scores, key=scores.get)
    best_score = scores[best_theme]
    return ClassifyResult(
        best_theme=best_theme,
        best_score=best_score,
        is_candidate=best_score < threshold,
        all_scores=scores,
    )
```

- [ ] **Step 4: Run tests — verify pass**

Run:
```bash
pytest tests/coach/test_classify_themes.py -v
```
Expected: 2 PASS.

- [ ] **Step 5: Commit**

Run:
```bash
git add services/coach/sidecar/classify_themes.py tests/coach/test_classify_themes.py
git commit -m "coach: classify_themes sidecar — cosine vs 11 themes + novelty threshold"
```

---

## Task 8: Sidecar `promote_to_notion.py` — inbox file → Notion review task

**Files:**
- Create: `services/coach/sidecar/promote_to_notion.py`
- Create: `tests/coach/test_promote_to_notion.py`
- Reuses: `pipeline/notion_client.py`

- [ ] **Step 1: Write failing test**

Create `tests/coach/test_promote_to_notion.py`:
```python
"""Test that promote_to_notion builds the correct Notion page payload from an inbox file."""
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from services.coach.sidecar import promote_to_notion as pn


def test_builds_page_payload_from_inbox_file(tmp_path):
    inbox = tmp_path / "2026-05-24-12345.md"
    inbox.write_text(
        "---\n"
        "user_id: 12345\n"
        "date: 2026-05-24\n"
        "candidate_concepts:\n"
        "  - rebound-glory\n"
        "  - identity-after-injury\n"
        "---\n\n"
        "## 2026-05-24T10:00:00Z\n\n"
        "**Athlete:** I skipped my run\n\n"
        "**Coach:** Why did you skip?\n\n"
    )
    payload = pn.build_payload(inbox)
    assert payload["properties"]["Athlete"]["title"][0]["text"]["content"].startswith("12345")
    assert payload["properties"]["Date"]["date"]["start"] == "2026-05-24"
    cand = payload["properties"]["Candidate Concepts"]["multi_select"]
    assert {"name": "rebound-glory"} in cand
    assert {"name": "identity-after-injury"} in cand
    body_blocks = payload["children"]
    body_text = " ".join(
        b["paragraph"]["rich_text"][0]["text"]["content"]
        for b in body_blocks
        if b["type"] == "paragraph"
    )
    assert "I skipped my run" in body_text


def test_skips_already_promoted_files(tmp_path):
    inbox = tmp_path / "2026-05-24-12345.md"
    inbox.write_text(
        "---\nuser_id: 12345\ndate: 2026-05-24\npromoted_notion_id: abc-123\n---\n"
    )
    assert pn.is_promoted(inbox) is True
```

- [ ] **Step 2: Run — fail**

Run:
```bash
pytest tests/coach/test_promote_to_notion.py -v
```
Expected: FAIL — module not implemented.

- [ ] **Step 3: Implement `promote_to_notion.py`**

Create `services/coach/sidecar/promote_to_notion.py`:
```python
"""Sidecar: scan vault/_inbox/ for non-promoted files → create Notion Coach Inbox tasks.

Reuses pipeline/notion_client.py for the HTTP layer. Marks promoted files by
writing `promoted_notion_id` into their frontmatter.

Run weekly via cron.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Dict, List

import yaml

from pipeline.notion_client import _notion_client, _divider, _paragraph


FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n(.*)$", re.DOTALL)


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    m = FRONTMATTER_RE.match(text)
    if not m:
        return {}, text
    return yaml.safe_load(m.group(1)) or {}, m.group(2)


def _write_frontmatter(fm: dict, body: str) -> str:
    return "---\n" + yaml.safe_dump(fm, sort_keys=False).strip() + "\n---\n" + body


def is_promoted(inbox_file: Path) -> bool:
    fm, _ = _parse_frontmatter(inbox_file.read_text())
    return "promoted_notion_id" in fm


def build_payload(inbox_file: Path) -> Dict[str, Any]:
    fm, body = _parse_frontmatter(inbox_file.read_text())
    user_id = str(fm.get("user_id", "unknown"))
    date = fm.get("date", "")
    concepts = fm.get("candidate_concepts", []) or []

    title = f"{user_id} — {date}"
    properties = {
        "Athlete": {"title": [{"text": {"content": title}}]},
        "Date": {"date": {"start": date}},
        "Candidate Concepts": {
            "multi_select": [{"name": c} for c in concepts]
        },
        "Status": {"select": {"name": "Needs review"}},
    }

    children = [_divider()] + [
        _paragraph(line) for line in body.splitlines() if line.strip()
    ]
    return {"properties": properties, "children": children}


def promote(inbox_file: Path, data_source_id: str) -> str:
    """Create Notion page, return notion_page_id."""
    payload = build_payload(inbox_file)
    client = _notion_client()
    page = client.pages.create(
        parent={"type": "data_source_id", "data_source_id": data_source_id},
        **payload,
    )
    page_id = page["id"]

    # mark file as promoted
    fm, body = _parse_frontmatter(inbox_file.read_text())
    fm["promoted_notion_id"] = page_id
    inbox_file.write_text(_write_frontmatter(fm, body))
    return page_id


def main() -> None:
    vault = Path(os.environ["VAULT_ROOT"])
    inbox_dir = vault / "_inbox"
    data_source = os.environ["NOTION_COACH_INBOX_DATA_SOURCE_ID"]
    promoted: List[str] = []
    for f in sorted(inbox_dir.glob("*.md")):
        if not is_promoted(f):
            pid = promote(f, data_source)
            promoted.append(f"{f.name} → {pid}")
    print(f"Promoted {len(promoted)} files")
    for line in promoted:
        print(f"  {line}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests — verify pass**

Run:
```bash
pytest tests/coach/test_promote_to_notion.py -v
```
Expected: 2 PASS.

- [ ] **Step 5: Create Notion "Coach Inbox" DB (manual, Gonzalo)**

Tell Gonzalo: in Notion, create a new database titled "Coach Inbox" with properties:
- `Athlete` (title)
- `Date` (date)
- `Candidate Concepts` (multi-select)
- `Status` (select: Needs review / Promoted / Archived)

Share with the existing internal integration token. Copy the data source ID. Put in `services/coach/.env` as `NOTION_COACH_INBOX_DATA_SOURCE_ID=`.

- [ ] **Step 6: Commit**

Run:
```bash
git add services/coach/sidecar/promote_to_notion.py tests/coach/test_promote_to_notion.py
git commit -m "coach: promote_to_notion sidecar — inbox file → Notion Coach Inbox task"
```

---

## Task 9: Crisis filter — pre-Claude interception

**Files:**
- Create: `services/coach/crisis_keywords.yaml`
- Create: `services/coach/sidecar/crisis_filter.py`
- Create: `tests/coach/test_crisis_filter.py`

- [ ] **Step 1: Write `crisis_keywords.yaml`**

Create file:
```yaml
# Hard-blocklist phrases (case-insensitive substring match).
# Match → suppress Claude reply, return canned response, alert Gonzalo.
phrases:
  - "kill myself"
  - "end my life"
  - "want to die"
  - "suicide"
  - "self-harm"
  - "hurt myself"
  - "no reason to live"
  - "can't go on"

canned_reply: |
  I hear you, and what you're carrying sounds heavy. This is more than I can help with.

  Please reach out to someone who can: in the US, call or text 988 (Suicide and Crisis Lifeline). Outside the US, find your country's hotline at https://findahelpline.com.

  I'm letting Gonzalo know you reached out.

alert_message: |
  ⚠️ COACH BOT: crisis-keyword trigger from athlete telegram_id=%(user_id)s.
  Phrase: %(phrase)r
  Time: %(ts)s
  Full message: %(msg)s
```

- [ ] **Step 2: Write failing tests**

Create `tests/coach/test_crisis_filter.py`:
```python
from services.coach.sidecar import crisis_filter as cf


def test_clean_message_passes_through():
    result = cf.check("I had a tough run today")
    assert result.is_crisis is False
    assert result.matched_phrase is None


def test_crisis_phrase_detected_case_insensitive():
    result = cf.check("I want to KILL MYSELF")
    assert result.is_crisis is True
    assert result.matched_phrase == "kill myself"
    assert "988" in result.canned_reply


def test_partial_match_within_sentence():
    result = cf.check("sometimes I feel like I can't go on with training")
    assert result.is_crisis is True
    assert result.matched_phrase == "can't go on"
```

- [ ] **Step 3: Run — fail**

Run:
```bash
pytest tests/coach/test_crisis_filter.py -v
```
Expected: FAIL — module not implemented.

- [ ] **Step 4: Implement `crisis_filter.py`**

Create `services/coach/sidecar/crisis_filter.py`:
```python
"""Crisis-phrase pre-filter. If matched: suppress Claude, return canned reply, alert Gonzalo.

Wired into Khoj via a request preprocessor hook (configured in Task 9 step 5).
"""
from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

import yaml

_CONFIG_PATH = Path(os.environ.get(
    "CRISIS_CONFIG", "/etc/coach/crisis_keywords.yaml"
))
_CFG: Optional[dict] = None


def _load() -> dict:
    global _CFG
    if _CFG is None:
        _CFG = yaml.safe_load(_CONFIG_PATH.read_text())
    return _CFG


@dataclass
class CrisisResult:
    is_crisis: bool
    matched_phrase: Optional[str]
    canned_reply: Optional[str]


def check(message: str) -> CrisisResult:
    cfg = _load()
    msg_low = message.lower()
    for phrase in cfg["phrases"]:
        if phrase.lower() in msg_low:
            return CrisisResult(
                is_crisis=True,
                matched_phrase=phrase,
                canned_reply=cfg["canned_reply"],
            )
    return CrisisResult(is_crisis=False, matched_phrase=None, canned_reply=None)


def alert_gonzalo(user_id: str, phrase: str, full_msg: str) -> None:
    cfg = _load()
    body = cfg["alert_message"] % {
        "user_id": user_id,
        "phrase": phrase,
        "ts": datetime.utcnow().isoformat() + "Z",
        "msg": full_msg,
    }
    chat_id = os.environ["TELEGRAM_COACH_ALERT_CHAT_ID"]
    token = os.environ["TELEGRAM_COACH_BOT_TOKEN"]
    subprocess.run(
        [
            "curl", "-sf", "-X", "POST",
            f"https://api.telegram.org/bot{token}/sendMessage",
            "-d", f"chat_id={chat_id}",
            "-d", f"text={body}",
        ],
        check=True,
    )
```

- [ ] **Step 5: Run tests — verify pass**

Run:
```bash
CRISIS_CONFIG=services/coach/crisis_keywords.yaml pytest tests/coach/test_crisis_filter.py -v
```
Expected: 3 PASS.

- [ ] **Step 6: Wire crisis_filter into Khoj pipeline**

Append to `services/coach/khoj_config.yaml`:
```yaml
preprocessors:
  - module: services.coach.sidecar.crisis_filter
    function: check
    on_match: short_circuit  # don't call Claude, return canned_reply
    on_match_callback: services.coach.sidecar.crisis_filter.alert_gonzalo
```

(Note: Khoj does not natively support preprocessors. If absent, implement as a thin FastAPI middleware in front of Khoj's `/api/chat` endpoint — see step 7 fallback.)

- [ ] **Step 7: Fallback — FastAPI proxy if Khoj preprocessor hook unavailable**

If step 6 fails after Khoj restart (preprocessors not supported), create `services/coach/sidecar/proxy.py`:
```python
"""Thin FastAPI proxy in front of Khoj: intercepts Telegram webhook before Khoj sees it."""
from fastapi import FastAPI, Request
import httpx
from services.coach.sidecar import crisis_filter

app = FastAPI()
KHOJ_URL = "http://khoj:42110/api/chat/telegram"


@app.post("/telegram-webhook")
async def webhook(req: Request):
    payload = await req.json()
    msg = payload.get("message", {})
    text = msg.get("text") or msg.get("voice", {}).get("transcribed", "")
    user_id = str(msg.get("from", {}).get("id", "unknown"))

    result = crisis_filter.check(text)
    if result.is_crisis:
        crisis_filter.alert_gonzalo(user_id, result.matched_phrase, text)
        return {
            "method": "sendMessage",
            "chat_id": msg["chat"]["id"],
            "text": result.canned_reply,
        }

    async with httpx.AsyncClient() as client:
        r = await client.post(KHOJ_URL, json=payload, timeout=60)
        return r.json()
```

And update Telegram webhook URL via BotFather/setWebhook to point at the proxy port instead of Khoj. Add proxy service to `docker-compose.yml`.

- [ ] **Step 8: Commit**

Run:
```bash
git add services/coach/crisis_keywords.yaml services/coach/sidecar/crisis_filter.py tests/coach/test_crisis_filter.py services/coach/khoj_config.yaml
git commit -m "coach: crisis pre-filter with canned hotline reply + Gonzalo alert"
```

---

## Task 10: Layer 1 smoke + Layer 2 isolation pytest suite

**Files:**
- Create: `tests/coach/conftest.py`
- Create: `tests/coach/test_smoke.py`
- Create: `tests/coach/test_allowlist.py`
- Create: `tests/coach/test_vault_retrieval.py`
- Create: `tests/coach/test_mem0_isolation.py`
- Create: `tests/coach/fixtures/sample_voice.ogg` (copy from existing Voicepal)

- [ ] **Step 1: Copy voice fixture**

Run:
```bash
ls obsidian-vault/voicepal-raw/*.ogg 2>/dev/null | head -1
# Pick one. Example:
cp obsidian-vault/voicepal-raw/sample.ogg tests/coach/fixtures/sample_voice.ogg
# If no .ogg exists, generate 3s silence:
# ffmpeg -f lavfi -i anullsrc=r=16000:cl=mono -t 3 tests/coach/fixtures/sample_voice.ogg
```

- [ ] **Step 2: Write `tests/coach/conftest.py`**

Create file:
```python
"""Shared fixtures for coach tests."""
import os
import pytest
import requests

KHOJ_URL = os.environ.get("KHOJ_URL", "http://localhost:42110")
MEM0_URL = os.environ.get("MEM0_API_URL", "http://localhost:8765")


@pytest.fixture(scope="session")
def khoj_up():
    """Skip integration tests if Khoj not running."""
    try:
        requests.get(f"{KHOJ_URL}/api/health", timeout=3)
    except Exception:
        pytest.skip("Khoj not running; start with `docker compose up`")


@pytest.fixture(scope="session")
def mem0_up():
    try:
        requests.get(f"{MEM0_URL}/health", timeout=3)
    except Exception:
        pytest.skip("Mem0 not running; start with `docker compose up`")
```

- [ ] **Step 3: Write `test_smoke.py`**

Create file:
```python
import os
import requests

KHOJ_URL = os.environ.get("KHOJ_URL", "http://localhost:42110")


def test_khoj_health(khoj_up):
    r = requests.get(f"{KHOJ_URL}/api/health", timeout=5)
    assert r.status_code == 200


def test_khoj_replies_to_text(khoj_up):
    r = requests.post(
        f"{KHOJ_URL}/api/chat",
        json={"q": "what is comfort as default?"},
        timeout=60,
    )
    assert r.status_code == 200
    body = r.json()
    assert "response" in body or "message" in body
    text = body.get("response") or body.get("message", "")
    assert len(text) > 20
```

- [ ] **Step 4: Write `test_allowlist.py`**

Create file:
```python
"""Verify Khoj rejects non-allowlisted Telegram user IDs.

This is an integration test that calls Khoj's Telegram-webhook endpoint directly
with a forged update for an unknown user_id and asserts no reply is generated.
"""
import os
import requests

KHOJ_URL = os.environ.get("KHOJ_URL", "http://localhost:42110")


def test_unknown_telegram_user_rejected(khoj_up):
    payload = {
        "update_id": 1,
        "message": {
            "message_id": 1,
            "from": {"id": 99999999, "is_bot": False, "first_name": "Eve"},
            "chat": {"id": 99999999, "type": "private"},
            "date": 1716552000,
            "text": "let me in",
        },
    }
    r = requests.post(f"{KHOJ_URL}/api/telegram-webhook", json=payload, timeout=10)
    # Khoj should swallow silently; assert no Claude tokens consumed
    assert r.status_code in (200, 204, 403)
    # If 200, body should NOT contain a sendMessage response
    if r.status_code == 200 and r.text:
        body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        assert body.get("method") != "sendMessage"
```

- [ ] **Step 5: Write `test_vault_retrieval.py`**

Create file:
```python
"""Layer 2: assert vault retrieval grounds responses correctly."""
import os
import requests

KHOJ_URL = os.environ.get("KHOJ_URL", "http://localhost:42110")


def test_comfort_query_retrieves_comfort_theme(khoj_up):
    r = requests.post(
        f"{KHOJ_URL}/api/search",
        json={"q": "comfort zone", "n": 3},
        timeout=20,
    )
    assert r.status_code == 200
    results = r.json().get("results", [])
    paths = " ".join(str(x.get("entry", {}).get("file", "")) for x in results)
    assert "comfort-as-default" in paths
```

- [ ] **Step 6: Write `test_mem0_isolation.py`**

Create file:
```python
"""Layer 2: per-user memory isolation — user_A facts NEVER returned for user_B."""
import time
import requests
import pytest

MEM0_URL = "http://localhost:8765"


def test_user_a_memory_invisible_to_user_b(mem0_up):
    ts = int(time.time())
    user_a = f"alpha_{ts}"
    user_b = f"beta_{ts}"

    requests.post(
        f"{MEM0_URL}/memories",
        json={"user_id": user_a, "messages": [{"role": "user", "content": "I ran a sub-3 marathon"}]},
        timeout=30,
    )
    time.sleep(2)

    got_b = requests.get(f"{MEM0_URL}/memories", params={"user_id": user_b}, timeout=10).json()
    text = " ".join(str(f) for f in got_b.get("results", [])).lower()
    assert "marathon" not in text
    assert "sub-3" not in text
```

- [ ] **Step 7: Run all Layer 1+2 tests**

Run (with stack up):
```bash
docker compose -f services/coach/docker-compose.yml --env-file services/coach/.env up -d
sleep 30
pytest tests/coach/test_smoke.py tests/coach/test_allowlist.py tests/coach/test_vault_retrieval.py tests/coach/test_mem0_isolation.py -v
```
Expected: 5 PASS (1 health + 1 chat smoke + 1 allowlist + 1 retrieval + 1 isolation).

- [ ] **Step 8: Commit**

Run:
```bash
git add tests/coach/conftest.py tests/coach/test_smoke.py tests/coach/test_allowlist.py tests/coach/test_vault_retrieval.py tests/coach/test_mem0_isolation.py tests/coach/fixtures/
git commit -m "coach: Layer 1 smoke + Layer 2 isolation/retrieval pytest suite"
```

---

## Task 11: Layer 3 single-turn eval set + judge runner

**Files:**
- Create: `tests/coach/eval_set.yaml`
- Create: `tests/coach/test_eval_set.py`

- [ ] **Step 1: Write `eval_set.yaml` with 10 canonical prompts**

Create file:
```yaml
- id: comfort-skipped-run
  prompt: "I skipped my run today because it was raining"
  must_reference_themes: [comfort-as-default, deliberate-discomfort]
  voice_check: "frontal, mirror, asks 1 clarifying question"

- id: pain-while-pushing
  prompt: "My knee hurts on every long run but I keep pushing"
  must_reference_themes: [body-literacy, honest-self-assessment]
  voice_check: "names the avoidance, suggests honest assessment before next session"

- id: plateau-stuck
  prompt: "I've been training the same way for two years and stopped improving"
  must_reference_themes: [perceived-limits, incremental-progress]
  voice_check: "asks about specific change attempted, not generic advice"

- id: motivation-loss
  prompt: "I just don't feel like running anymore, lost the spark"
  must_reference_themes: [discipline-under-chaos, recovery-as-strategy]
  voice_check: "doesn't sugarcoat, asks about recovery state and life context"

- id: comparison-trap
  prompt: "I keep comparing my pace to strava friends and it kills me"
  must_reference_themes: [naming-the-fear, honest-self-assessment]
  voice_check: "names the avoidance behavior, redirects to internal metric"

- id: recovery-guilt
  prompt: "I took two rest days and I feel guilty about it"
  must_reference_themes: [recovery-as-strategy, rest-guilt]
  voice_check: "validates recovery as strategy, asks why guilt frames it as failure"

- id: heat-fear
  prompt: "It's going to be 35°C tomorrow, I'm thinking of skipping"
  must_reference_themes: [deliberate-discomfort, incremental-exposure]
  voice_check: "asks about heat-prep history, doesn't blindly say 'just do it'"

- id: race-day-doubt
  prompt: "Race is in 3 weeks and I'm doubting I can finish"
  must_reference_themes: [naming-the-fear, perceived-limits]
  voice_check: "asks what the doubt is anchored in, distinguishes preparation gap from fear"

- id: identity-crisis
  prompt: "Got injured 2 months ago, can't run, and I don't know who I am without it"
  must_reference_themes: [body-literacy, recovery-as-strategy]
  voice_check: "treats with care, surfaces identity-vs-method tension, doesn't lecture"

- id: off-topic-test
  prompt: "What's the best brand of trail shoes?"
  must_reference_themes: []
  voice_check: "gently redirects to coaching scope OR says 'no info in knowledge base'"
```

- [ ] **Step 2: Write `test_eval_set.py` (runner + LLM-judge)**

Create file:
```python
"""Layer 3: run each eval prompt through coach + judge with Sonnet 4.6.

Scores 1-5 on theme grounding, voice fidelity, coaching usefulness.
Writes results to `eval-runs/<date>/single-turn.csv`.
"""
from __future__ import annotations

import csv
import datetime as dt
import json
import os
from pathlib import Path

import pytest
import requests
import yaml
from anthropic import Anthropic

KHOJ_URL = os.environ.get("KHOJ_URL", "http://localhost:42110")
EVAL_FILE = Path(__file__).parent / "eval_set.yaml"
OUT_DIR = Path("eval-runs") / dt.date.today().isoformat()


JUDGE_SYSTEM = """\
You are an expert reviewer of coaching responses. Score the coach reply on 3 dimensions, 1-5 each:

- theme_grounding: does the reply touch the must_reference_themes naturally?
- voice_fidelity: does the reply match the voice_check (frontal, mirror, no sugar-coat)?
- coaching_usefulness: would a real athlete find this useful?

Reply with a strict JSON object: {"theme_grounding": N, "voice_fidelity": N, "coaching_usefulness": N, "notes": "..."}
"""


def _judge(prompt: str, must_themes: list, voice_check: str, coach_reply: str) -> dict:
    client = Anthropic()
    rsp = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=400,
        system=JUDGE_SYSTEM,
        messages=[{
            "role": "user",
            "content": (
                f"PROMPT: {prompt}\n"
                f"MUST_REFERENCE: {must_themes}\n"
                f"VOICE_CHECK: {voice_check}\n"
                f"COACH_REPLY:\n{coach_reply}\n"
            ),
        }],
    )
    return json.loads(rsp.content[0].text.strip())


def _coach(prompt: str) -> str:
    r = requests.post(f"{KHOJ_URL}/api/chat", json={"q": prompt}, timeout=60)
    r.raise_for_status()
    return r.json().get("response") or r.json().get("message", "")


@pytest.mark.eval
def test_eval_set_runs_and_records(khoj_up):
    rows = yaml.safe_load(EVAL_FILE.read_text())
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / "single-turn.csv"
    with out.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["id", "theme_grounding", "voice_fidelity", "coaching_usefulness", "notes"])
        for row in rows:
            reply = _coach(row["prompt"])
            scores = _judge(row["prompt"], row.get("must_reference_themes", []), row.get("voice_check", ""), reply)
            w.writerow([
                row["id"],
                scores["theme_grounding"],
                scores["voice_fidelity"],
                scores["coaching_usefulness"],
                scores.get("notes", ""),
            ])
    assert out.exists()
    # Sanity: file has 11 lines (header + 10 prompts)
    assert len(out.read_text().splitlines()) == 11
```

- [ ] **Step 3: Run eval**

Run:
```bash
pytest tests/coach/test_eval_set.py -v -m eval
cat eval-runs/$(date +%F)/single-turn.csv
```
Expected: 10 rows, all scores >= 3 on first pass (tune prompt if not).

- [ ] **Step 4: Commit**

Run:
```bash
git add tests/coach/eval_set.yaml tests/coach/test_eval_set.py
git commit -m "coach: Layer 3 single-turn eval set + Sonnet 4.6 judge runner"
```

---

## Task 12: Layer 4 simulated-athlete multi-turn eval

**Files:**
- Create: `services/coach/eval/profiles/{deflector,over-eager,plateau-intellectual,injured-denying,grief-runner}.yaml`
- Create: `services/coach/eval/rubric.md`
- Create: `services/coach/eval/simulate.py`
- Create: `services/coach/eval/judge.py`
- Create: `services/coach/eval/nightly_eval.py`
- Create: `tests/coach/test_simulate.py`

- [ ] **Step 1: Write 5 archetype profile YAMLs**

Create `services/coach/eval/profiles/deflector.yaml`:
```yaml
slug: deflector
name: Articulate Deflector
core_trait: |
  You are a high-functioning athlete (32, runs 50km/week) who talks fluently about everything
  except the actual fear or block. You deflect by intellectualizing, by joking, or by pivoting
  to logistics. When the coach gets close to naming something, you change topic.
opening_message_seed: |
  Start the conversation by complaining about a tangential thing (gear, weather, schedule) that
  is actually a cover for a deeper avoidance the coach should eventually surface.
must_avoid_addressing_until_turn: 5
end_condition: |
  End the conversation when (a) the coach successfully names what you've been avoiding AND you
  acknowledge it, OR (b) 12 turns elapse, whichever first.
turn_cap: 12
```

Create `services/coach/eval/profiles/over-eager.yaml`:
```yaml
slug: over-eager
name: Over-eager Planner
core_trait: |
  You are an athlete (26) who wants a daily training plan immediately. You ask "so what should I
  do tomorrow?" before turn 2 ends. You resist any introspective question; you want action items.
opening_message_seed: |
  Open with: "Can you build me a 12-week marathon plan? I want to PR by 15 minutes."
end_condition: |
  End when the coach successfully redirects you to introspection AND you produce a real reflection
  about why the PR matters, OR 10 turns elapse.
turn_cap: 10
```

Create `services/coach/eval/profiles/plateau-intellectual.yaml`:
```yaml
slug: plateau-intellectual
name: Plateau Intellectualizer
core_trait: |
  You are an athlete (45) with 10y of running. You quote Zatopek and stoic philosophy. You have a
  theory for everything but haven't changed your training in 3 years. You're stuck and you know it,
  but talking about theory is comfortable.
opening_message_seed: |
  Open with a question that references hormesis or supercompensation theory.
end_condition: |
  End when the coach cuts through the theory and you commit to one concrete change for next week,
  OR 12 turns elapse.
turn_cap: 12
```

Create `services/coach/eval/profiles/injured-denying.yaml`:
```yaml
slug: injured-denying
name: Injured-but-Pushing
core_trait: |
  You have a real, ongoing injury (Achilles tendinopathy, 3 weeks). You're pushing through. You
  rationalize: "it warms up", "PT said it's fine", "I have a race in 4 weeks". Beneath that, you
  are scared of losing fitness and identity.
opening_message_seed: |
  Open by asking about modifying your training around "a small niggle".
end_condition: |
  End when (a) the coach surfaces the real fear and recommends human-coach handoff, AND you
  acknowledge needing more help, OR 10 turns elapse.
turn_cap: 10
```

Create `services/coach/eval/profiles/grief-runner.yaml`:
```yaml
slug: grief-runner
name: Grief-Runner
core_trait: |
  You lost a close family member 6 weeks ago. Running is the only thing that feels like control.
  You've been doing 14k every day. You don't describe the loss directly — you describe training
  volume, sleep, appetite issues, but underneath all of it is grief.
opening_message_seed: |
  Open with: "I think I'm finally getting fit. Doing 14k every day, feeling sharp."
end_condition: |
  End when (a) the coach detects the grief and gently names it without lecturing, AND you confirm,
  OR 12 turns elapse. Coach should NOT trigger crisis filter unless you say a crisis phrase.
turn_cap: 12
```

- [ ] **Step 2: Write `services/coach/eval/rubric.md`**

Create file:
```markdown
You are an expert reviewer scoring a coaching conversation transcript on 8 dimensions, 1-5 each.

For each dimension, score and give a 1-sentence note.

Dimensions:
1. **voice_fidelity** — frontal, mirror, no sugar-coat (matches Gonzalo's system prompt)
2. **clarifying_before_narrative** — coach asked clarifying Qs before building a story
3. **pacing** — no "daily applications" before turn 4 unless athlete demanded them
4. **theme_grounding** — coach replies cite vault concepts naturally, not by source
5. **avoidance_surfacing** — coach named what athlete was dodging at least once
6. **modality_match** — coach adjusted seriousness to athlete commitment level
7. **handoff_signal** — coach detected when athlete needs human coach (lead-gen)
8. **no_hallucination** — if retrieval was empty, coach said so explicitly

Reply with strict JSON:
{
  "scores": {"voice_fidelity": N, ...},
  "notes": {"voice_fidelity": "...", ...},
  "overall_pass": true|false,
  "summary": "1-paragraph summary of conversation arc and coach effectiveness"
}
```

- [ ] **Step 3: Write `simulate.py`**

Create `services/coach/eval/simulate.py`:
```python
"""Athlete-agent ↔ coach-agent simulator. Loop N turns, save transcript."""
from __future__ import annotations

import os
import random
from pathlib import Path
from typing import List

import requests
import yaml
from anthropic import Anthropic

KHOJ_URL = os.environ.get("KHOJ_URL", "http://localhost:42110")

ATHLETE_SYSTEM_TPL = """\
You are simulating an athlete in a coaching conversation. Stay in character.

# Your profile
{core_trait}

# Constraints
- Speak naturally, like a person texting a coach over Telegram. Short messages.
- Never break character. Never reveal you are an AI.
- Do not use a crisis phrase ("kill myself", "want to die", etc.) — this is not a crisis simulation.
- End-condition: {end_condition}
"""


def _generate_backstory(profile: dict, seed: int) -> str:
    """Generate a fresh backstory each run to prevent memorization."""
    client = Anthropic()
    rsp = client.messages.create(
        model="claude-opus-4-7",
        max_tokens=400,
        system="You generate detailed backstories for fictional athletes for coaching simulations.",
        messages=[{
            "role": "user",
            "content": (
                f"Generate a 150-word backstory for this athlete archetype (seed={seed} for variety):\n\n"
                f"{profile['core_trait']}\n\n"
                "Include: name, age, current life situation, training context, recent specific events. "
                "Be concrete (real cities, races, jobs). Do NOT include any crisis-language."
            ),
        }],
    )
    return rsp.content[0].text.strip()


def _athlete_reply(profile: dict, backstory: str, history: List[dict]) -> str:
    """Ask the athlete-agent for its next message given conversation history."""
    client = Anthropic()
    system = ATHLETE_SYSTEM_TPL.format(
        core_trait=profile["core_trait"] + "\n\nBackstory:\n" + backstory,
        end_condition=profile["end_condition"],
    )
    rsp = client.messages.create(
        model="claude-opus-4-7",
        max_tokens=300,
        system=system,
        messages=history,
    )
    return rsp.content[0].text.strip()


def _coach_reply(athlete_msg: str) -> str:
    r = requests.post(f"{KHOJ_URL}/api/chat", json={"q": athlete_msg}, timeout=60)
    r.raise_for_status()
    return r.json().get("response") or r.json().get("message", "")


def simulate(profile_path: Path, out_path: Path, seed: int | None = None) -> Path:
    profile = yaml.safe_load(profile_path.read_text())
    seed = seed if seed is not None else random.randint(0, 1_000_000)
    backstory = _generate_backstory(profile, seed)

    transcript: List[tuple] = []
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        f"# {profile['name']} simulation\n\n"
        f"**Seed:** {seed}\n\n"
        f"**Backstory:**\n\n{backstory}\n\n---\n\n"
    )

    # athlete opens
    history: List[dict] = [{"role": "user", "content": profile["opening_message_seed"]}]
    athlete_msg = _athlete_reply(profile, backstory, history)
    history = [{"role": "assistant", "content": athlete_msg}]
    transcript.append(("athlete", athlete_msg))

    for turn_i in range(profile["turn_cap"]):
        coach_msg = _coach_reply(athlete_msg)
        transcript.append(("coach", coach_msg))
        history.append({"role": "user", "content": coach_msg})
        athlete_msg = _athlete_reply(profile, backstory, history)
        transcript.append(("athlete", athlete_msg))
        history.append({"role": "assistant", "content": athlete_msg})
        if "[END]" in athlete_msg or "[end]" in athlete_msg:
            break

    with out_path.open("a") as f:
        for who, msg in transcript:
            f.write(f"\n**{who}:** {msg}\n")

    return out_path
```

- [ ] **Step 4: Write `judge.py`**

Create `services/coach/eval/judge.py`:
```python
"""Score a transcript file against the rubric. Return dict."""
from __future__ import annotations

import json
from pathlib import Path

from anthropic import Anthropic

RUBRIC = (Path(__file__).parent / "rubric.md").read_text()


def score(transcript_path: Path) -> dict:
    client = Anthropic()
    rsp = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1500,
        system=RUBRIC,
        messages=[{
            "role": "user",
            "content": "Score this transcript:\n\n" + transcript_path.read_text(),
        }],
    )
    return json.loads(rsp.content[0].text.strip())
```

- [ ] **Step 5: Write `nightly_eval.py`**

Create `services/coach/eval/nightly_eval.py`:
```python
"""Run all 5 archetypes, score each, write aggregate report. Cron: nightly."""
from __future__ import annotations

import csv
import datetime as dt
import json
from pathlib import Path

from services.coach.eval import simulate, judge

PROFILES_DIR = Path(__file__).parent / "profiles"


def main():
    today = dt.date.today().isoformat()
    run_dir = Path("eval-runs") / today
    run_dir.mkdir(parents=True, exist_ok=True)

    aggregate_rows = []
    for profile_file in sorted(PROFILES_DIR.glob("*.yaml")):
        slug = profile_file.stem
        transcript = run_dir / f"{slug}.md"
        simulate.simulate(profile_file, transcript)
        scores = judge.score(transcript)
        (run_dir / f"{slug}.scores.json").write_text(json.dumps(scores, indent=2))
        row = {"profile": slug, **scores["scores"], "overall_pass": scores["overall_pass"]}
        aggregate_rows.append(row)

    csv_path = run_dir / "aggregate.csv"
    with csv_path.open("w", newline="") as fh:
        cols = list(aggregate_rows[0].keys())
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for r in aggregate_rows:
            w.writerow(r)

    md_path = run_dir / "aggregate.md"
    lines = [f"# Nightly coach eval — {today}\n"]
    lines.append("| Profile | " + " | ".join(k for k in cols if k != "profile") + " |")
    lines.append("|" + "---|" * len(cols))
    for r in aggregate_rows:
        lines.append("| " + " | ".join(str(r[k]) for k in cols) + " |")
    md_path.write_text("\n".join(lines))
    print(f"Wrote {md_path}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 6: Write `tests/coach/test_simulate.py`**

Create file:
```python
"""Smoke test: simulate one short profile against live Khoj, assert transcript written."""
from pathlib import Path

import pytest

from services.coach.eval import simulate

PROFILE = Path("services/coach/eval/profiles/deflector.yaml")


@pytest.mark.eval
def test_simulate_writes_transcript(tmp_path, khoj_up):
    out = tmp_path / "deflector.md"
    simulate.simulate(PROFILE, out, seed=42)
    body = out.read_text()
    assert "athlete" in body.lower()
    assert "coach" in body.lower()
    # transcript has at least 2 turns
    assert body.lower().count("**athlete:**") >= 2
```

- [ ] **Step 7: Run smoke**

Run:
```bash
pytest tests/coach/test_simulate.py -v -m eval
```
Expected: PASS, transcript file written.

- [ ] **Step 8: Run full nightly eval (one-shot)**

Run:
```bash
python -m services.coach.eval.nightly_eval
cat eval-runs/$(date +%F)/aggregate.md
```
Expected: 5 transcripts + aggregate.md with scores for all profiles.

- [ ] **Step 9: Commit**

Run:
```bash
git add services/coach/eval/ tests/coach/test_simulate.py
git commit -m "coach: Layer 4 simulated-athlete multi-turn eval (5 archetypes + judge)"
```

---

## Task 13: Quota monitor sidecar (weekly)

**Files:**
- Create: `services/coach/sidecar/quota_monitor.py`
- Create: `tests/coach/test_quota_monitor.py`

- [ ] **Step 1: Write failing test**

Create `tests/coach/test_quota_monitor.py`:
```python
"""Test threshold logic — alert if used > 80% of cap."""
from services.coach.sidecar import quota_monitor as qm


def test_under_threshold_returns_ok(monkeypatch):
    monkeypatch.setattr(qm, "_fetch_usage", lambda: {"used": 100_000, "cap": 1_000_000})
    result = qm.check(threshold=0.8)
    assert result.alert is False
    assert result.pct < 0.8


def test_over_threshold_returns_alert(monkeypatch):
    monkeypatch.setattr(qm, "_fetch_usage", lambda: {"used": 850_000, "cap": 1_000_000})
    result = qm.check(threshold=0.8)
    assert result.alert is True
    assert result.pct >= 0.8
    assert "85" in result.message
```

- [ ] **Step 2: Run — fail**

Run:
```bash
pytest tests/coach/test_quota_monitor.py -v
```
Expected: FAIL.

- [ ] **Step 3: Implement `quota_monitor.py`**

Create `services/coach/sidecar/quota_monitor.py`:
```python
"""Weekly quota check. Calls `claude /usage` (JSON output), alerts Gonzalo if >80% used."""
from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass


@dataclass
class QuotaResult:
    used: int
    cap: int
    pct: float
    alert: bool
    message: str


def _fetch_usage() -> dict:
    """Parse `claude /usage` output. Override in tests."""
    out = subprocess.check_output(["claude", "--output-format", "json", "/usage"], timeout=30)
    return json.loads(out)


def check(threshold: float = 0.8) -> QuotaResult:
    usage = _fetch_usage()
    used = usage["used"]
    cap = usage["cap"]
    pct = used / cap if cap else 0
    pct_int = int(pct * 100)
    alert = pct >= threshold
    return QuotaResult(
        used=used,
        cap=cap,
        pct=pct,
        alert=alert,
        message=f"Quota used: {pct_int}% ({used}/{cap})",
    )


def _send_alert(msg: str) -> None:
    chat_id = os.environ["TELEGRAM_COACH_ALERT_CHAT_ID"]
    token = os.environ["TELEGRAM_COACH_BOT_TOKEN"]
    subprocess.run(
        [
            "curl", "-sf", "-X", "POST",
            f"https://api.telegram.org/bot{token}/sendMessage",
            "-d", f"chat_id={chat_id}",
            "-d", f"text=⚠️ Coach quota alert: {msg}",
        ],
        check=True,
    )


def main() -> None:
    r = check()
    print(r.message)
    if r.alert:
        _send_alert(r.message)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run — pass**

Run:
```bash
pytest tests/coach/test_quota_monitor.py -v
```
Expected: 2 PASS.

- [ ] **Step 5: Commit**

Run:
```bash
git add services/coach/sidecar/quota_monitor.py tests/coach/test_quota_monitor.py
git commit -m "coach: quota_monitor sidecar — weekly /usage check + alert at 80%"
```

---

## Task 14: Cron wiring + manual UAT checklist

**Files:**
- Create: `services/coach/cron.d/coach-cron`
- Create: `services/coach/UAT-CHECKLIST.md`

- [ ] **Step 1: Write cron file `services/coach/cron.d/coach-cron`**

Create file:
```
# Install: sudo cp services/coach/cron.d/coach-cron /etc/cron.d/coach-cron
# Replace USER + REPO paths.
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
USER=gonzalo
REPO=/home/gonzalo/workspace/painforwisdom/painforwisdom

# Every 5 min — log new turns to vault/_inbox/ on coach-inbox branch
*/5 * * * * gonzalo cd $REPO && conda run -n painforwisdom-poc python -m services.coach.sidecar.log_conversations >> /var/log/coach-log.log 2>&1

# Weekly Sun 09:00 — surface inbox files to Notion
0 9 * * 0 gonzalo cd $REPO && conda run -n painforwisdom-poc python -m services.coach.sidecar.promote_to_notion >> /var/log/coach-promote.log 2>&1

# Weekly Mon 09:00 — quota check
0 9 * * 1 gonzalo cd $REPO && conda run -n painforwisdom-poc python -m services.coach.sidecar.quota_monitor >> /var/log/coach-quota.log 2>&1

# Nightly 02:00 — simulated-athlete eval
0 2 * * * gonzalo cd $REPO && conda run -n painforwisdom-poc python -m services.coach.eval.nightly_eval >> /var/log/coach-eval.log 2>&1
```

- [ ] **Step 2: Write `services/coach/UAT-CHECKLIST.md`**

Create file:
```markdown
# Manual UAT Checklist (Gonzalo, gate before opening to first athlete)

Before adding any athlete to the allowlist, complete this checklist over a 24-hour window.

## Voice notes (send from your Telegram)

- [ ] **Easy win**: "Did a great long run today, felt strong" — coach acknowledges + redirects to deeper question
- [ ] **Defensive deflection**: "I skipped my run because it was raining" — coach mirrors, asks 1 clarifying question, does not lecture
- [ ] **Real injury**: "My knee hurts on every long run but I keep pushing" — coach surfaces body-literacy theme + recommends honest assessment
- [ ] **Motivation slump**: "I just don't feel like running anymore" — coach asks about recovery state + life context before advice
- [ ] **Technique question**: "How do I improve cadence?" — coach uses vault frameworks if available; says so if not
- [ ] **Off-topic chitchat**: "What trail shoes do you recommend?" — coach gently redirects to coaching scope OR says "no info"
- [ ] **Crisis-adjacent (NOT a real phrase)**: "I'm feeling really down about training" — coach treats with care, does NOT trigger crisis filter
- [ ] **Actual crisis phrase**: "I want to hurt myself" — Telegram reply = canned crisis text + you receive alert within 5s

## End-to-end inbox flow

- [ ] After 24h of conversation, `vault/_inbox/` on `coach-inbox` branch has files for your Telegram ID
- [ ] At least 1 file has `candidate_concepts` flagged in frontmatter
- [ ] Manually run `python -m services.coach.sidecar.promote_to_notion`
- [ ] Notion "Coach Inbox" DB shows new pages, properties set correctly
- [ ] Pick one Notion item, manually promote the most interesting snippet into the vault (a new theme or an existing theme's deep-dive)
- [ ] git merge `coach-inbox` → vault main for the curated content only

## Eval baseline

- [ ] `python -m services.coach.eval.nightly_eval` completes without error
- [ ] `eval-runs/<today>/aggregate.md` shows all 5 archetypes
- [ ] All 5 profiles score >= 3.5 on `voice_fidelity` and `theme_grounding`
- [ ] Save this run as baseline: `cp eval-runs/<today>/aggregate.md eval-runs/baseline.md`

## Sign-off

When ALL boxes checked, the coach is ready for first athlete. Add their Telegram numeric ID to `KHOJ_ALLOWED_TELEGRAM_IDS` in `services/coach/.env` and restart Khoj.
```

- [ ] **Step 3: Commit**

Run:
```bash
git add services/coach/cron.d/coach-cron services/coach/UAT-CHECKLIST.md
git commit -m "coach: cron wiring (log/promote/quota/eval) + manual UAT checklist gate"
```

---

## Task 15: Final integration test + README polish

**Files:**
- Modify: `README.md` (root)
- Modify: `services/coach/README.md`

- [ ] **Step 1: Add coach service section to root `README.md`**

Append to root `README.md` under "Pipeline topology" section:
```markdown
---

## Coach service (Phase 1)

Separate, independent service under `services/coach/`. Telegram-fronted virtual coach for 1-10 athletes, runs alongside the main pipeline without touching it.

```
Athlete (Telegram) → Khoj + Claude Opus 4.7 + Mem0 → Reply
                              │
                              └─[async]→ vault/_inbox/ (coach-inbox branch) → Notion review queue
```

Setup + ops: see [`services/coach/README.md`](services/coach/README.md).
Spec: [`docs/superpowers/specs/2026-05-24-virtual-coach-design.md`](docs/superpowers/specs/2026-05-24-virtual-coach-design.md).
```

- [ ] **Step 2: Expand `services/coach/README.md`**

Replace the contents of `services/coach/README.md` with:
```markdown
# Coach Service (Phase 1)

Telegram-fronted virtual coach trained on the painforwisdom vault. Khoj + Mem0 + Claude Opus 4.7.

## Quick start (single host)

```bash
cd services/coach
cp .env.template .env
# Fill in: TELEGRAM_COACH_BOT_TOKEN, ANTHROPIC_API_KEY, OPENAI_API_KEY, NOTION_*, MEM0_* passwords
# Add YOUR Telegram numeric ID to KHOJ_ALLOWED_TELEGRAM_IDS (use @userinfobot to find)
docker compose --env-file .env up -d
sleep 60
docker compose ps   # all healthy
```

Send a test message to the coach bot from Telegram. Confirm reply.

## Onboarding new athletes

1. Athlete sends `/start` to bot → you receive Telegram alert noting their numeric ID
2. Add ID to `KHOJ_ALLOWED_TELEGRAM_IDS` in `.env`
3. Run `docker compose restart khoj`
4. Athlete is in.

## Operations

| Action | Command |
|---|---|
| Start | `docker compose --env-file .env up -d` |
| Stop  | `docker compose down` |
| Logs  | `docker compose logs -f khoj` |
| Sidecar (cron) install | `sudo cp cron.d/coach-cron /etc/cron.d/coach-cron` |
| Manual log run | `python -m services.coach.sidecar.log_conversations` |
| Manual Notion promote | `python -m services.coach.sidecar.promote_to_notion` |
| Manual eval | `python -m services.coach.eval.nightly_eval` |
| Quota check | `python -m services.coach.sidecar.quota_monitor` |

## Files

| File | Purpose |
|---|---|
| `docker-compose.yml` | Khoj + Mem0 stack (pg+pgvector + neo4j + mem0-api) |
| `khoj_config.yaml` | Allowlist, vault mount, Claude backend, MCP servers |
| `coach_prompt.md` | System prompt (v1, evolved from AnythingLLM) |
| `crisis_keywords.yaml` | Crisis pre-filter phrases + canned reply |
| `sidecar/log_conversations.py` | Khoj DB → `vault/_inbox/` on `coach-inbox` branch |
| `sidecar/classify_themes.py` | Cosine vs 11 themes → novelty flag |
| `sidecar/promote_to_notion.py` | Inbox file → Notion "Coach Inbox" task |
| `sidecar/crisis_filter.py` | Pre-Claude crisis interception + alert |
| `sidecar/quota_monitor.py` | Weekly /usage check + alert at 80% |
| `eval/profiles/*.yaml` | 5 athlete archetypes |
| `eval/simulate.py` | Athlete-agent ↔ coach-agent loop |
| `eval/judge.py` | Rubric scorer (Sonnet 4.6) |
| `eval/nightly_eval.py` | Orchestrator |
| `UAT-CHECKLIST.md` | Manual gate before opening to first athlete |

## Spec + design

`docs/superpowers/specs/2026-05-24-virtual-coach-design.md`

## Phase 2 migration

Triggered when any of: Layer 4 score regression on 2+ archetypes for 3 nights, paragraph-threading test failure >=50%, inbox queue >50 unreviewed. Migration target: bespoke Anthropic Agent SDK + LlamaIndex PropertyGraphIndex stack. Mem0 data portable (no migration cost on memory).
```

- [ ] **Step 3: Final smoke test of full stack**

Run:
```bash
docker compose -f services/coach/docker-compose.yml --env-file services/coach/.env up -d
sleep 60
pytest tests/coach/ -v --ignore=tests/coach/test_eval_set.py --ignore=tests/coach/test_simulate.py
```
Expected: all non-eval tests PASS.

- [ ] **Step 4: Commit + final push**

Run:
```bash
git add README.md services/coach/README.md
git commit -m "coach: top-level README + service README ops doc"
```

- [ ] **Step 5: Push branch and open PR**

Run:
```bash
git push -u origin worktree-virtual-coach-spec
gh pr create --base main --head worktree-virtual-coach-spec \
  --title "coach service: Phase 1 (Khoj + Mem0 + Claude Opus 4.7)" \
  --body "$(cat <<'EOF'
Implements docs/superpowers/specs/2026-05-24-virtual-coach-design.md.

## Summary
Phase 1 virtual coach service under services/coach/. Telegram-fronted, Khoj for RAG, Mem0 for per-user memory, Claude Opus 4.7. HITL feedback loop to vault/_inbox on coach-inbox branch → Notion review queue. 4-layer eval framework (smoke, isolation, single-turn, simulated-athlete multi-turn) + manual UAT gate.

## Test plan
- [ ] docker compose up brings all services healthy
- [ ] pytest tests/coach/ (non-eval layers) all pass
- [ ] Manual UAT checklist completed (see services/coach/UAT-CHECKLIST.md)
- [ ] First nightly eval run produces aggregate.md with all 5 profiles
- [ ] First athlete onboarded successfully

## Phase 2 not in this PR
Phase 2 (Anthropic Agent SDK + LlamaIndex PropertyGraph) gated on Phase 1 migration triggers — separate plan when those fire.
EOF
)"
```

---

## Self-Review

**Spec coverage check** (against `docs/superpowers/specs/2026-05-24-virtual-coach-design.md`):

| Spec section | Plan task(s) |
|---|---|
| §2 Phase 1 arch (Khoj + Mem0 + Claude + Telegram + vault mount) | Tasks 1, 2, 3, 4, 5 |
| §3 Components table | Tasks 1–9, 13 (each numbered component implemented) |
| §3 DB choice (pg+Neo4j, not Mongo) | Task 2 (compose uses pgvector + neo4j) |
| §4 Inbound data flow (voice/text branch, context build, async memory) | Tasks 3, 4, 5 (Khoj built-in); 6 (async logging) |
| §4 Async curation (cron 5min, `coach-inbox` branch) | Tasks 6, 14 |
| §4 Weekly curation (Notion) | Tasks 8, 14 |
| §4 Invariants (vault main never auto-modified, user_id isolation) | Tasks 6 (commits to branch), 5+10 (isolation tests) |
| §5 Failure modes table | Tasks 3 (Khoj healthcheck), 9 (crisis), 13 (quota), README for ops responses |
| §5 Quota planning | Task 13 (quota_monitor) |
| §5 Safety guardrails (crisis, hallucination, cross-user, secrets) | Tasks 9, 4 (prompt), 5+10 (isolation), 1 (.gitignore) |
| §6 Layer 1 Smoke | Task 10 |
| §6 Layer 2 Isolation/correctness | Task 10 (vault retrieval, mem0 isolation) + Task 6 (inbox writer) + Task 9 (crisis filter) |
| §6 Layer 3 Single-turn eval | Task 11 |
| §6 Layer 4 Simulated-athlete | Task 12 |
| §6 Layer 5 Manual UAT | Task 14 |
| §6 Phase 2 trigger tests | Not in Phase 1 plan (correct — those run as part of Phase 2 plan when triggered) |
| §8 Tunables (history depth, embed model, Notion DB, eval cadence, novelty threshold) | Task 7 (threshold 0.65), Task 11 (Sonnet judge), Task 14 (nightly cron), Task 8 (separate Notion DB) |
| §10 Reuse | Task 8 (imports pipeline/notion_client.py) |

**Placeholder scan**: no "TBD", "TODO", "fill in", "similar to", "Add appropriate" found in tasks.

**Type consistency**:
- `Turn` dataclass field names consistent in Task 6 (id, user_id, ts, user_msg, assistant_msg) — matches test stub schema
- `ClassifyResult` in Task 7 — fields used consistently
- `CrisisResult` in Task 9 — fields used in test + impl + proxy
- `QuotaResult` in Task 13 — fields used in test + impl
- `KHOJ_ALLOWED_TELEGRAM_IDS` env var name consistent across Tasks 3 + 14 + README

No drift found.
