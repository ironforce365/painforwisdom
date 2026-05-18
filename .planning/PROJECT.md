# Voicenote — Long-Form Voice Capture for Book Vault

## What This Is

A new capture surface in the `painforwisdom` repo that turns long-form Spanish voice notes (sent via Telegram, plus a one-shot backfill from Voicepal's Notion archive) into atomic English coaching-thought entries in the existing `obsidian-vault/gonzalo-book/` vault. Each long note typically contains 2–4 distinct thoughts; the new module splits, translates, and hands each chunk to the existing `coaching-thought-extractor` + `kb-curator` agents — so new entries flow into the same themes and book outline as today's video-derived entries.

This is purely Gonzalo's. No multi-user, no SaaS, no public bot.

## Core Value

**More book-grade coaching thoughts captured per week, without paying Voicepal and without losing nuance from long Spanish voice notes.**

If everything else fails, this must work: a Spanish voice message sent via Telegram lands as one or more atomic English entries in the vault, reviewed before commit, slotted under the right themes, and traceable back to the source audio.

## Requirements

### Validated

<!-- Existing capabilities from `pipeline/` + vault that this project relies on. Not built by this project. -->

- ✓ Local Whisper transcription pipeline — existing (`pipeline/` + `extract_transcription.sh`)
- ✓ Coaching-thought extraction from short raw thoughts — existing (`coaching-thought-extractor` agent)
- ✓ Vault entry / theme / book-outline maintenance — existing (`kb-curator` agent, `obsidian-vault/gonzalo-book/`)
- ✓ Notion REST integration with internal token + data-source IDs — existing (`pipeline/notion_client.py`)
- ✓ Telegram bot infrastructure (separate daily-brief bot already running) — existing
- ✓ LangGraph pipeline runtime, retry, watchdog, structured logging — existing

### Active

<!-- v1 scope for this project. Hypotheses until shipped. -->

- [ ] **Voicenote module scaffold** — new `voicenote/` package next to `pipeline/`; shared deps; reuses runtime helpers
- [ ] **Long-note pipeline** — transcribe → split (ES, LLM) → translate each chunk → existing `coaching-thought-extractor` per chunk
- [ ] **Source adapter abstraction** — `voicenote/sources/{telegram,notion}.py` produce the same `LongNote` object; rest of pipeline is source-agnostic
- [ ] **Notion backfill (one-shot)** — ingest all 29 Voicepal subpages under [Voicepal pages](https://www.notion.so/Voicepal-pages-35b5901befa980d3bb58c1a5fc1ce7b3) into the vault
- [ ] **Telegram capture (ongoing)** — new dedicated bot; cron poll every N min; long-form voice messages → vault entries
- [ ] **User-id allowlist** — bot only accepts messages from Gonzalo's Telegram user_id (single ID, hardcoded/env-var, silent reject otherwise)
- [ ] **Review-before-commit UX** — bot replies with N draft entries; user confirms via inline buttons; only on confirm does `kb-curator` write to vault
- [ ] **Audio retention** — keep `.ogg` per entry under `voicenote/audio/YYYY-MM-DD-slug.ogg` (gitignored, replayable on extraction failure)
- [ ] **Source frontmatter** — each entry has `source: telegram://msg/<id>` or `source: notion://page/<id>` + `parent_note: <id>` linking siblings split from the same note
- [ ] **Overlap flagging (not skip)** — if extracted thought looks like an existing vault entry, write it anyway but tag `[[possible-duplicate-of:<slug>]]` for later human review
- [ ] **Operations: visibility + idempotency** — track last polled Telegram `update_id`, processed Notion page IDs; safe to resume after crash; structured logs to match existing `pipeline/` conventions

### Out of Scope

- **Voicepal subscription / Voicepal app integration** — sub will be cancelled; no live Voicepal API. The only Voicepal touchpoint is the one-shot Notion read.
- **Multi-user / public bot** — single-user, allowlist-gated. No registration, no per-user state.
- **Real-time transcription** — cron poll is fine; users (you) can wait 5–15 min for processing.
- **Hosted / cloud transcription vendors (OpenAI Whisper API, Deepgram, AssemblyAI)** — reusing local Whisper for v1; revisit only if Spanish quality is unacceptable.
- **Auto-commit without review** — every long note goes through a human-confirm step; ambiguity is a feature, not a bug.
- **Semantic / embedding-based dedup** — overlap is flagged textually for v1, not embedded-compared. Defer until duplicate volume justifies it.
- **Feeding voicenotes into the existing short-thought LangGraph pipeline** — that pipeline is built for short raw thoughts; long voice notes have a different shape and get their own module.
- **Vault PR / git-review per entry** — entries land directly via `kb-curator` after Telegram confirm. No per-entry branch/PR overhead.
- **Notion staging DB before vault** — backfill writes straight to vault (with review prompt via Telegram if needed); no extra Notion review step.
- **Telegram webhook server** — cron poll instead, to avoid a public-URL/tunnel dependency.
- **Translation provider other than the existing LLM stack** — reuse the same Anthropic/LLM calls already wired into `pipeline/`.

## Context

**Repo state (from `.planning/codebase/`):**

- `pipeline/` is a Python LangGraph DAG that takes a short raw thought (typically a 30–90s video transcript) → `coaching-thought-extractor` → `kb-curator` → research → blog → Notion → audio/YT. Loud-fail philosophy, stdlib `unittest`, `python-telegram-bot` already in deps.
- `obsidian-vault/gonzalo-book/` is the canonical content store: 45 entries, 16 themes, 8 frameworks, an auto-maintained `book-outline.md`. Currently 100% English.
- Notion REST is used direct (`pipeline/notion_client.py`); MCP path is only used by older Paperclip-era agents.
- Recent commits (May 2026) hardened error recovery, watchdog, daily-brief Telegram delivery, and 1M-context beta gating — this project will inherit those patterns, not re-invent them.

**Source material to backfill (Notion):**

- 29 subpages under [Voicepal pages](https://www.notion.so/Voicepal-pages-35b5901befa980d3bb58c1a5fc1ce7b3). Mix of EN and ES titles; bodies are mostly Spanish, cleaned (not raw) but conversational — single subpage commonly carries 2–4 distinct thoughts (e.g., "Pain is the currency of growth" contains: growth-vs-progress, two-types-of-pain, expectation-vs-reality).
- Same thoughts have, in some cases, also been captured via video and already live as English vault entries. Overlap is real and recurring → hence the flag-don't-skip policy.

**Vault status alert (from `.planning/codebase/CONCERNS.md`):**

- `obsidian-vault` submodule is currently dirty (uncommitted entries). The voicenote writer must respect the submodule boundary and commit through it explicitly. Carry the same git-hygiene the existing pipeline has been refactoring toward.

## Constraints

- **Tech stack:** Python 3.x, plain Python pipeline (NOT LangGraph for the voicenote flow — linear, doesn't need DAG/checkpoints), `python-telegram-bot==22.7` (one explicit new dep — verified NOT already in repo; current Telegram path is curl-via-`telegram_io.sh` which is insufficient for inline keyboards + getFile + CallbackQuery), stdlib `unittest`, `notion-client`, local Whisper (bumped to `large-v3` for voicenote runs). **Minimal new deps with explicit rationale; PTB is the only addition.**
- **Repo:** Single repo (`painforwisdom`). New module is `voicenote/`, sibling to `pipeline/`. No standalone service, no second deployment target.
- **Auth:** Gonzalo only. Telegram `user_id` allowlist. All other senders silently rejected. (Closes the `_wait_reply` chat-id gap noted in `CONCERNS.md`.)
- **Vendor cost:** No paid transcription vendor. Local Whisper only for v1. Translation uses the Anthropic LLM already wired in.
- **Latency:** Not real-time. Cron poll interval (likely 5–15 min) is acceptable.
- **Storage:** Raw `.ogg` retained per entry but `.gitignored`. Vault entries committed through the `obsidian-vault` submodule.
- **Privacy:** Notes and audio are personal. Nothing leaves the local host except (a) LLM API calls, (b) Telegram getUpdates polls, (c) Notion reads, (d) Notion blog writes already handled by `pipeline/`.
- **Quota:** User has Anthropic Ultra/Max subscription — token budget is generous, but prompts must still be bounded (cf. recent error-recovery prompt-growth fix). No silent feature drops if a planned LLM choice can't fit.

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| New dedicated Telegram bot (not extension of painforwisdom bot) | Keeps voice-capture concerns isolated from daily-brief; clean separation of tokens and webhooks | — Pending |
| New `voicenote/` module in same repo (not standalone service) | Reuses runtime, LLM helpers, Notion client, vault writer; one deploy target | — Pending |
| Reuse local Whisper (not OpenAI/Deepgram) | Zero new vendor; Spanish quality re-evaluated after backfill | — Pending |
| Reuse existing `coaching-thought-extractor` + `kb-curator` agents | Same downstream contract as video pipeline; new entries are indistinguishable in vault | — Pending |
| Processing order: transcribe → split (ES) → translate per chunk → extract per chunk | Preserves Spanish nuance through segmentation; avoids "translation flattens before split" failure mode | — Pending |
| Vault stays English-uniform; preserve ES verbatim per entry | Pitfalls research raised ES→EN voice flattening risk. AnythingLLM embedder consideration: mixed-language vault breaks EN-only embedders, only works with multilingual embedders. Locked: English entry + `source_es:` frontmatter + `## Source (ES)` body section retains the original voice; vault retrieval stays language-uniform; ES voice recoverable on demand | — Pending |
| Cron poll (not webhook) for Telegram | No public URL or tunnel needed; matches existing scheduled-job ops pattern | — Pending |
| Review-before-commit via Telegram inline buttons | Long voice → multiple entries is high-leverage but error-prone; human confirm is cheap insurance | — Pending |
| Overlap with existing entries → flag, don't skip | Avoids losing nuance from a second pass on the same thought; manual reconciliation cheaper than missing material | — Pending |
| Source adapter abstraction (Telegram / Notion → same `LongNote` object) | DRY between ongoing capture and one-shot Voicepal backfill; future sources cheap to add | — Pending |
| `user_id` allowlist on bot | Closes chat-id-gap noted in `CONCERNS.md`; matches single-user reality | — Pending |
| Keep raw `.ogg` per entry | Replay on extraction failure; cheap on disk; gitignored | — Pending |
| Frontmatter: `source:` + `parent_note:` | Full traceability — vault entry → source audio/page; siblings linked across the same long note | — Pending |
| Backfill (29 Voicepal subpages) ships in Phase 2 — early | Features research: 29 subpages = natural test corpus for splitter prompt; validates dual-source abstraction before Telegram complexity layers on; idempotency + dry-run de-risk irreversible vault writes | — Pending |
| Plain Python pipeline for voicenote (NOT LangGraph) | Architecture research: flow is linear (transcribe → split → translate → extract → review → commit) with one async pause (Telegram review). LangGraph DAG/`interrupt()` over-spec. Existing LangGraph runtime stays untouched; voicenote runs alongside | — Pending |
| State persistence in SQLite `voicenote/state/voicenote.db` | Tables: `long_notes` (status state machine), `drafts` (per-chunk approval), `telegram_cursor` (idempotency), `processed_notion_pages` (backfill dedup). Single source of truth across cron ticks | — Pending |
| Bot lifecycle: short-lived drain via systemd user timer | Matches existing `painforwisdom-daily-brief.timer` ops pattern; no persistent daemon; same heal-then-notify watchdog shape | — Pending |
| Build order is PoC-first: P0 validates Spanish Whisper quality + LLM split reliability | Per `feedback_poc_before_migration` memory; these are the two HIGH-risk assumptions; if either fails the architecture pivots before any module scaffolding | — Pending |
| One cross-boundary touch into existing `pipeline/`: refactor `vault_writer.py:apply_curation_plan` as a shared helper + add `commit_vault_submodule()` | Closes existing `[high]` CONCERNS.md bug (kb_curator writes but never commits); both pipelines share one vault-write code path | — Pending |
| Cost forecast extension: `pipeline.cost_forecast --voicenote` before any backfill or large replay | Per `feedback_cost_forecast_before_replay` memory; bound LLM token quota share before running 29-subpage backfill or long voice notes | — Pending |

## Evolution

This document evolves at phase transitions and milestone boundaries.

**After each phase transition** (via `/gsd-transition`):
1. Requirements invalidated? → Move to Out of Scope with reason
2. Requirements validated? → Move to Validated with phase reference
3. New requirements emerged? → Add to Active
4. Decisions to log? → Add to Key Decisions
5. "What This Is" still accurate? → Update if drifted

**After each milestone** (via `/gsd-complete-milestone`):
1. Full review of all sections
2. Core Value check — still the right priority?
3. Audit Out of Scope — reasons still valid?
4. Update Context with current state

---
*Last updated: 2026-05-18 after initialization + post-research adjustments*
