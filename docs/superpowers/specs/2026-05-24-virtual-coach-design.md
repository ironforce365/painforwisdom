# Virtual Coach on the Painforwisdom Vault — Design

**Status**: Design sections 1–5 approved via brainstorming 2026-05-24; awaiting Gonzalo review of written spec before plan handoff
**Owner**: Gonzalo
**Stack**: Khoj + Mem0 + Claude Opus 4.7 (Phase 1) → Anthropic Agent SDK + LlamaIndex PropertyGraph + Mem0 + Notion-inbox HITL (Phase 2)
**Research basis**: `/home/gonzalo/.claude/jobs/4b96cef6/coach-research.md`, `/home/gonzalo/.claude/jobs/4b96cef6/mem0-db-compare.md`

---

## 1. Goal and constraints

Build a Telegram-fronted virtual coach trained on Gonzalo's Obsidian knowledge base. Pilot serves 1–10 trusted athletes. Product positioning: lead generation for Gonzalo's human coaching practice — coach must mirror his voice well enough that strong-fit athletes self-identify and seek human follow-up, while weaker-fit users still receive useful, grounded guidance.

Constraints:
- **Scale**: 1–10 athletes in first 3–6 months
- **Modality**: voice OR text input from athletes; text output from coach (voice output deferred to later phase)
- **Curation**: human-in-the-loop — conversations never auto-mutate vault canon; candidates surface to Notion review queue
- **Latency budget**: coach reply <20s end-to-end (Whisper + retrieval + Claude)
- **Cost**: subscription quota, not USD; Opus 4.7 quota burn tracked per `/usage` weekly
- **Brand**: coach voice fidelity > scale > feature breadth

## 2. Two-phase architecture

### Phase 1 (Week 1–2): Khoj + Mem0 + Claude

```
Athlete (Telegram voice/text)
        │
        ▼
[Khoj bot 8986...781A]  ── multi-user, allowlist by Telegram ID
        │
        ├─▶ Whisper (voice → text) — Khoj built-in
        ├─▶ Khoj RAG over obsidian-vault/ (read-only mount)
        ├─▶ Anthropic Claude Opus 4.7 (backend)
        └─▶ Mem0 MCP tool ── per-user facts (vector + graph)
        │
        ▼
Reply (text) → Telegram
        │
        ▼
[Async, cron 5min] sidecar/log_conversations.py → vault/_inbox/YYYY-MM-DD-<userId>.md
                   git commit + push branch "coach-inbox" (NEVER vault main)
[Weekly] promote_to_notion.py → Notion "Coach Inbox" DB; Gonzalo curates → manual merge to vault main
```

Containers (single Docker compose under `services/coach/`):
- `khoj` — bot + RAG + Whisper, Claude backend, vault mounted `:ro`
- `mem0-postgres` (Postgres + pgvector) — vector + structured store
- `mem0-neo4j` — graph store
- `mem0-api` — Mem0 HTTP API, MCP server exposed to Khoj

### Phase 2 (Week 3+): Stack C bespoke

Trigger migration when any of:
- Khoj retrieval consistently misses paragraph-level threading
- Coach voice drifts under multi-turn pressure (eval scores drop)
- Inbox-HITL loop requires Khoj fork to extend

Replacements:
| Phase 1 | Phase 2 |
|---|---|
| Khoj container | `services/coach/` FastAPI app on `claude-agent-sdk` |
| Khoj RAG | LlamaIndex `PropertyGraphIndex` built nightly from `ObsidianReader` (wikilinks = typed edges) |
| Khoj Telegram | `claude-plugins-official/external_plugins/telegram` (official Anthropic plugin) |
| Khoj user mgmt | Allowlist YAML + per-user `/memories/<telegram_id>/` (Claude `memory_20250818` tool) |
| Inbox logger sidecar | First-class `post_turn_hook` in coach service |

Mem0 unchanged across phases; data portable.

## 3. Components

### Phase 1 file layout

```
services/
  coach/
    docker-compose.yml          # khoj + mem0 stack
    .env.template               # bot tokens, ANTHROPIC, MEM0 creds
    khoj_config.yaml            # allowlist, vault mount, Claude backend
    coach_prompt.md             # system prompt (versioned, evolves from current AnythingLLM prompt)
    sidecar/
      log_conversations.py      # Khoj convo DB → vault/_inbox/
      promote_to_notion.py      # inbox file → Notion review task
      classify_themes.py        # cosine vs 11-theme embeddings → novelty score
    eval/
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
.env.coach.template
eval-runs/                      # gitignored output dir
```

### Component responsibilities

| # | Component | Tech | Purpose |
|---|---|---|---|
| 1 | Coach bot | Khoj | Telegram fronting, RAG, calls Claude |
| 2 | Vault mount | `bind :ro` | Read-only vault access |
| 3 | Memory store | Mem0 (pg+pgvector + Neo4j) | Per-user facts, scope `user_id=<telegram_id>` |
| 4 | Voice transcribe | Khoj Whisper | Voice msg → text |
| 5 | Allowlist | Khoj user config | Whitelist 1–10 Telegram IDs |
| 6 | Conversation logger | Python sidecar (cron 5min) | Khoj DB → `vault/_inbox/` on `coach-inbox` branch |
| 7 | Inbox→Notion bridge | reuse `notion_research.py` pattern | Inbox file → Notion task with snippets + theme suggestions |
| 8 | Coach system prompt | `coach_prompt.md` | Versioned, evolved from existing AnythingLLM prompt |
| 9 | Eval framework | Layered (smoke, isolation, retrieval, simulated-athlete) | Quality + regression guard |

### DB choice: stay with Postgres + Neo4j

Evaluated MongoDB as single-DB swap. Verdict (per `mem0-db-compare.md`): MongoDB serves Mem0 vector role but NOT graph role (Mem0 graph supports Neo4j/Memgraph/Kuzu/Neptune only). Self-hosted MongoDB vector search still Preview, requires `mongot` sidecar + replica-set mode, and Mem0's MongoDB driver had a deprecated-index bug fix Feb 2026. Stay with pg+pgvector + Neo4j (Mem0 default). If single-DB later becomes a goal, evaluate FalkorDB or Memgraph instead.

## 4. Data flow

### Inbound turn — voice OR text

```
1. Athlete sends Telegram message → bot 8986...781A
2. Khoj checks allowlist (telegram_user_id)
       │ not allowed → silent reject
3. Branch on type:
     ├─ voice → download → Whisper → transcript_text
     └─ text  → msg.text → transcript_text
4. Khoj builds context:
     ├─ system_prompt    = coach_prompt.md
     ├─ vault_retrieval  = Khoj RAG top-k chunks
     ├─ user_memory      = Mem0.get_memories(user_id, query=transcript_text)
     └─ recent_history   = last N turns from Khoj per-user session
5. Khoj → Claude Opus 4.7 (single call)
6. Reply → Telegram (text)
7. Async (post-reply, non-blocking):
     ├─ Mem0.add_memories(user_id, turn) — fact extraction + vector/graph upsert
     └─ Append to Khoj per-user convo DB
```

### Async curation (cron 5min)

```
sidecar/log_conversations.py:
  for each new turn since last_run:
    append to vault/_inbox/YYYY-MM-DD-<userId>.md
    classify_themes.py:
      for each athlete utterance:
        embed → cosine vs 11 vault theme embeddings
        if max_sim < 0.65 → flag candidate_concept
    write candidates to file frontmatter
  if any new file today:
    git commit + push → branch "coach-inbox" (NEVER main)
```

### Curation (weekly, manual)

```
promote_to_notion.py:
  for each new inbox file not yet in Notion:
    create Notion "Coach Inbox" task: anonymized userId, candidates, snippets, file link
  Telegram notify: "N new inbox items"
Gonzalo reviews in Notion → promotes selected snippets via manual vault edits
Periodically: git merge "coach-inbox" → vault main (curated only)
```

### Invariants

1. Vault main never auto-modified — all auto-writes go to `coach-inbox` branch
2. Mem0 per-user isolation — `user_id` filter on every call, asserted in integration test
3. Reply latency only includes Whisper + retrieval + Claude — memory + logging are post-reply async
4. No raw athlete content reaches blog/Notion-research pipelines — `coach-inbox` is sealed from main LangGraph DAG

## 5. Error handling and quota

### Failure modes

| Failure | Detection | Response |
|---|---|---|
| Whisper transcribe fails | Khoj error | Reply: "couldn't hear that, can you send text or try again?" + log |
| Vault retrieval empty | top-k = 0 | Proceed without retrieval; Claude relies on system prompt + memory; log warning |
| Mem0 down | 5xx / timeout | Skip memory fetch + skip memory write; reply still flows; Telegram alert to Gonzalo |
| Claude quota exhausted | 429 | Reply: "I'm overloaded right now, give me a few minutes" + Telegram alert |
| Claude auth fails | 401 | Reply: "service issue, Gonzalo notified" + Telegram alert |
| Khoj container down | bot unresponsive | Docker healthcheck + cron probe → Telegram alert + restart |
| Telegram rate limit | 429 | Exponential backoff, drop oldest if buffer > 100 |
| Inbox cron fails | non-zero exit | Telegram alert, retry next 5min tick |
| Notion API fails | 5xx | Retry 3x, queue to disk for next run |
| Git push to coach-inbox fails | non-zero exit | Stop, alert; manual resolve (likely conflict) |

### Quota planning

Per athlete turn (Opus 4.7):
- Input: ~3k tokens (system 1k + retrieval 1.5k + memory 0.3k + history 0.2k)
- Output: ~500 tokens
- Total: ~3.5k tokens/turn

Pilot load: 10 athletes × 20 turns/day = 200 turns/day = **700k tokens/day** for live coach traffic.

Eval load: nightly simulated-athlete eval = **~550k tokens/run** (5 profiles × 10 turns × 2 agents × 4k + backstory + judge), reducible to ~150k by routing backstory + judge to Sonnet 4.6.

Phase 1 (Khoj on subscription, pre-2026-06-15): single quota bucket; pilot fits with headroom.

Phase 2 (Stack C with Agent SDK, post-2026-06-15): Agent SDK draws **separate** subscription quota bucket. Monitor `/usage` weekly during Phase 1; set Telegram alert if projected Phase 2 demand exceeds 80% of bucket cap. Fallback: swap Opus 4.7 → Sonnet 4.6 per-turn when quota tight (system-prompt invariant: never degrade voice fidelity silently — log the swap).

### Safety guardrails

| Guardrail | Mechanism |
|---|---|
| Refuse non-coaching topics | System-prompt directive + post-hoc filter |
| Prevent vault hallucination | Coach prompt: "if retrieval is empty, say so explicitly" |
| Self-harm / crisis | Hard-coded reply with hotline + Telegram alert to Gonzalo; **Claude reply suppressed**; crisis-keyword list versioned |
| Cross-user leak | Mem0 query enforces `user_id` filter at API; integration test asserts isolation |
| Secrets in conversation transcripts | `.env` only, never committed; bot token rotated via `@BotFather` if leaked |

## 6. Testing

### Layer 1 — Smoke

| Test | Method | Pass |
|---|---|---|
| Khoj boots + answers stub turn | `pytest tests/coach/test_smoke.py` | 200 OK, non-empty reply |
| Allowlist rejects unknown ID | Mock TG update, random user_id | No Claude call |
| Voice path end-to-end | Fixture `.ogg` (existing Voicepal sample) | Transcript non-empty, reply generated |
| Text path end-to-end | Mock TG text update | Reply generated, retrieval hits > 0 |

### Layer 2 — Isolation + correctness

| Test | Method | Pass |
|---|---|---|
| Mem0 per-user isolation | Seed user_A=foo, user_B=bar; query user_B | No foo returned |
| Vault retrieval grounding | Query "comfort zone" | `themes/comfort-as-default.md` in top-3 |
| Async memory write | Turn + sleep 2s + Mem0 query | Fact appears |
| Inbox writer | Manual cron run after seeded turn | File exists on `coach-inbox` branch |
| Crisis filter | Send trigger phrase | Hard-coded reply, Claude NOT called, Telegram alert fired |

### Layer 3 — Single-turn eval set

`tests/coach/eval_set.yaml` — 10–15 canonical prompts drawn from vault `thoughts/` + `entries/`. Each row:

```yaml
- prompt: "I skipped my run today because it was raining"
  must_reference_themes: [comfort-as-default, deliberate-discomfort]
  must_not: ["sugar-coat", "permissive"]
  voice_check: "frontal, mirror, asks 1 clarifying question"
```

Run weekly. Claude Sonnet 4.6 judge scores 1–5 on: theme grounding, voice fidelity, coaching usefulness. Track in `eval-results.csv`. Alert if any score drops > 0.5.

### Layer 4 — Simulated-athlete multi-turn eval

Five archetypes, fresh backstory per run (prevents memorization), athlete-agent ↔ coach-agent loop 8–12 turns, judge scores transcript.

Archetypes:
1. **Articulate deflector** — high-functioning, talks fluently, dodges commitment. Tests accountability-mirror trait.
2. **Over-eager planner** — wants daily plan turn 1. Tests "don't jump to applications" + manufactured-suffering detection.
3. **Plateau intellectualizer** — 10y experience, stuck, theorizes everything. Tests grounding + cutting through abstraction.
4. **Injured-but-pushing** — real pain, denying. Tests body-literacy theme + human-coach handoff signal.
5. **Grief-runner** — recent loss, blurs grief with training. Tests gentleness + crisis-adjacent escalation.

Architecture:
```
nightly_eval.py
  for profile in 5 archetypes:
    backstory = Claude Opus 4.7 generate (random seed, fresh each run)
    athlete_agent = ClaudeAgent(system = profile_card + backstory)
    coach_agent   = production coach endpoint
    simulate 8–12 turns (athlete-agent stops when natural-end or cap)
    transcript → eval-runs/YYYY-MM-DD/<profile>.md
    judge_agent (Sonnet 4.6, fresh session) scores on rubric
  aggregate_report.md with per-profile scores + diff vs last run + regression flags
```

Rubric (judge scores 1–5):
- Voice fidelity (frontal, mirror, no sugar-coat)
- Clarifying-before-narrative
- Pacing (no daily applications before turn 4 unless demanded)
- Theme grounding (cites vault concepts naturally, not by source)
- Avoidance surfacing (names what athlete dodges at least once)
- Modality match (adjusts seriousness to commitment level)
- Lead-gen handoff signal (detects when athlete needs human coach)
- No hallucination (says so if retrieval empty)

### Layer 5 — Manual UAT (Gonzalo-driven, gate before opening to athletes)

10 voice notes covering: easy win, defensive deflection, real-injury question, motivation slump, technique question, off-topic chitchat, crisis-adjacent. Verify voice fidelity, inbox files generated, candidate concepts surfaced, end-to-end promote-one-item Notion → vault.

### Phase 2 migration trigger tests

When Khoj fails these, trigger Stack C migration:
- Paragraph-level threading: athlete sends multi-paragraph dump → each paragraph's response cites correct paragraph-anchored theme
- PropertyGraph hop: query touching theme A → expect chunk from entry that wikilinks A (graph-aware retrieval)

## 7. Out of scope (Phase 1)

- Voice output (TTS) — deferred to post-pilot
- Public Telegram discovery — invite-only with explicit allowlist
- Per-athlete billing / payment integration
- Multi-language coach (English-only at PoC)
- Auto-promote inbox → vault main (always manual via Notion review)
- Cross-athlete pattern detection / cohort analytics
- Replacing existing painforwisdom blog/research pipeline DAG (coach service is independent)

## 8. Tunables (defaults chosen, revisit if signals warrant)

These are decided for PoC; listed here so plan-phase knows the knobs:

- **Conversation history depth** sent to Claude each call: **N=10 turns**. Tune after Layer 4 eval data.
- **Embedding model** for `classify_themes.py`: **`text-embedding-3-small`** via OpenAI (already in `.env`). Switch if novelty scores noisy.
- **Notion DB for Coach Inbox**: **new dedicated DB** (not the Research DB) — keeps athlete conversation data isolated from research/blog data.
- **Layer 4 eval cadence**: **nightly during pilot ramp**, scale back to weekly once scores stabilize for 2 weeks.
- **Novelty threshold** in `classify_themes.py`: cosine `< 0.65` flags as candidate concept. Calibrate after first 100 utterances.

## 9. Phase 2 migration triggers

Migration to Stack C triggered when **any one** of:
- Layer 4 average score drops below 3.5 on >=2 archetypes for 3 consecutive runs (voice/quality regression Khoj can't fix)
- Paragraph-threading test fails on >=50% of multi-paragraph dumps (RAG architecture mismatch)
- HITL inbox queue > 50 unreviewed items because Khoj's surfacing is too coarse (loop architecture mismatch)

Migration **precondition** (gate, not trigger): Phase 2 Agent SDK quota bucket projected sufficient for current usage trajectory + 50% headroom. If trigger fires but precondition fails, fall back to Sonnet 4.6 on Phase 1 first and reassess.

## 10. Dependencies and reuse

| Existing asset | Reused for |
|---|---|
| `extract_transcription.sh` (Whisper wrapper) | Voice fallback if Khoj Whisper insufficient |
| `pipeline/notion_*.py` | Inbox→Notion bridge pattern |
| `obsidian-vault/` git submodule | Read-only mount for Khoj + sidecar write target on `coach-inbox` branch |
| Telegram bot infra (existing `telegram_io.sh`) | Alert channel for ops events |
| Anthropic OAuth subscription path | Claude backend (Phase 1 + Phase 2) |
| Existing 11-theme + 8-framework vault structure | Classifier ontology |

---

**Approved sections**: 1, 2, 3, 4, 5, 6 (design content). Awaiting spec self-review + Gonzalo review before invoking writing-plans.
