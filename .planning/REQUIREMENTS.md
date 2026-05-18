# Requirements: Voicenote

**Defined:** 2026-05-18
**Core Value:** More book-grade coaching thoughts captured per week, without paying Voicepal and without losing nuance from long Spanish voice notes.

## v1 Requirements

Requirements for initial release. Each will map to roadmap phases.

### Pre-Flight (POC)

Validate riskiest assumptions before any module scaffolding. Per `feedback_poc_before_migration` memory.

- [ ] **POC-01**: Spanish Whisper transcription quality is acceptable on ≥3 real Voicepal-style voice notes (`large-v3` or `large-v3-turbo`; document chosen model + WER signal)
- [ ] **POC-02**: LLM splitter reliably segments a long ES transcript into N atomic thoughts on ≥3 fixtures (target: ≥80% of human-judged boundary agreement; document prompt + edge cases)
- [ ] **POC-03**: `coaching-thought-extractor` agent produces equivalent-quality output when fed a translated chunk vs a native short transcript (sanity-check existing extractor on new input shape)
- [ ] **POC-04**: Voicepal kill-list — inventory and document any residual Voicepal integrations (webhooks, scheduled syncs, etc.) before subscription cancellation
- [ ] **POC-05**: `pipeline.cost_forecast --voicenote` extension projects tokens + cost + Anthropic quota share for backfill + typical Telegram capture cadence (per `feedback_cost_forecast_before_replay` memory)

### Capture — Shared (CAP)

The `LongNote` abstraction + cross-source plumbing both Telegram and Notion paths depend on.

- [ ] **CAP-01**: `voicenote/sources/{telegram,notion}.py` produce the same `LongNote` object (`id`, `source`, `language`, `text`, `audio_path?`, `captured_at`, `raw_metadata`); rest of pipeline is source-agnostic
- [ ] **CAP-02**: SQLite state at `voicenote/state/voicenote.db` with tables `long_notes` (state machine), `drafts`, `telegram_cursor`, `processed_notion_pages`
- [ ] **CAP-03**: Raw `.ogg` retained per Telegram entry under `voicenote/audio/YYYY-MM-DD-slug.ogg`; gitignored; indexed by Telegram message id
- [ ] **CAP-04**: `.gitignore` updated to exclude `voicenote/audio/`, `voicenote/state/`, `voicenote/dryrun/`, `voicenote/pending/`

### Capture — Telegram (TG)

Ongoing Telegram capture surface. Single-user, allowlist-gated, cron-driven.

- [ ] **TG-01**: New dedicated Telegram bot registered via @BotFather; token in `VOICENOTE_TELEGRAM_BOT_TOKEN` env var (separate from existing painforwisdom bot)
- [ ] **TG-02**: `user_id` allowlist via `VOICENOTE_ALLOWED_USER_ID` env var; all other senders silently rejected (closes `_wait_reply` chat-id gap)
- [ ] **TG-03**: `python-telegram-bot==22.7` added to `pipeline/requirements.txt` with explicit rationale comment (one new dep, sole exception to "minimal new deps" constraint)
- [ ] **TG-04**: Voice messages downloaded via `getFile`; **pre-flight `file_size` check rejects >20 MB** with user-facing reply explaining the cap
- [ ] **TG-05**: Telegram poll cursor (`last_update_id`) persisted in `voicenote/state/voicenote.db` (telegram_cursor table); safe to resume after restart with no message reprocessing
- [ ] **TG-06**: Whisper transcription pinned to `--language es` (no auto-detect, avoid stuck-in-English code-switch trap)

### Capture — Notion Backfill (NTN)

One-shot ingestion of the 29 Voicepal subpages. Ships in Phase 2 (early) as test corpus for splitter.

- [ ] **NTN-01**: Read 29 subpages under [Voicepal pages](https://www.notion.so/Voicepal-pages-35b5901befa980d3bb58c1a5fc1ce7b3) via existing `pipeline/notion_client.py`; handle pagination + child-block recursion
- [ ] **NTN-02**: Each subpage → one `LongNote` (same adapter contract as Telegram source); downstream pipeline identical
- [ ] **NTN-03**: Idempotency — processed Notion page IDs persisted to `processed_notion_pages` table; safe re-run skips already-done pages
- [ ] **NTN-04**: Dry-run mode renders would-write entries to `voicenote/dryrun/<run-id>/` without committing to vault; inspect before `--apply`
- [ ] **NTN-05**: Notion REST pacing reuses existing `_PACE_SECONDS = 0.4` from `pipeline/notion_client.py` (~2.5 req/s, under Notion's 3 req/s cap)

### Process — Split, Translate, Extract (PROC)

The shared per-LongNote pipeline, source-agnostic.

- [ ] **PROC-01**: LLM splitter operates on full ES transcript, returns ordered atomic chunks with reasoning (Anthropic `tool_use` JSON output via existing `pipeline.llm.call_llm`)
- [ ] **PROC-02**: Caption parsing — `/N` in Telegram caption forces N splits (overrides auto-detect); remainder becomes title hint
- [ ] **PROC-03**: Per-chunk translator ES → EN via existing LLM stack; output preserves the original ES chunk verbatim in a `source_excerpt_es` payload field
- [ ] **PROC-04**: Each translated chunk fed to existing `coaching-thought-extractor` agent (unchanged contract — same input shape as short-transcript path)
- [ ] **PROC-05**: Per-chunk parallelism via `ThreadPoolExecutor(max_workers=3)`; LLM rate-limit respected; Whisper stays sequential (flock-protected)
- [ ] **PROC-06**: Translator returns confidence flag (`HIGH/MEDIUM/LOW`) — surfaced in Telegram review preview as ⚠️ on `LOW` (code-switched / ambiguous chunks)
- [ ] **PROC-07**: Splitter + translator prompts loaded from `.claude/agents/voicenote-splitter.md` and `voicenote-translator.md` with frontmatter + `CACHE_PADDING_APPENDIX` (match existing prompt-loading convention)

### Review — Inline Keyboard UX (REV)

Per-chunk approval via Telegram inline keyboards. Locked decision over text-reply pattern.

- [ ] **REV-01**: Bot posts one preview message per draft chunk (not per long-note) — supports partial approval when one chunk in a 4-split is a duplicate
- [ ] **REV-02**: Each preview shows: title (bold), English body, source excerpt (ES), confidence flag (if LOW), possible-duplicate link (if flagged), [Approve] / [Reject] inline buttons
- [ ] **REV-03**: `CallbackQueryHandler` calls `answer_callback_query` **within 1 s of receipt** (well inside Telegram's 15 s timeout) before doing any heavy work; heavy work happens after the ack
- [ ] **REV-04**: Drafts persisted in `voicenote/state/voicenote.db` (drafts table) keyed by message id; survive process restart and cron-tick boundaries
- [ ] **REV-05**: 24-hour expiry on pending drafts (cron sweep moves stale → `voicenote/pending/expired/` archive; never auto-commits, never silently discards)
- [ ] **REV-06**: Approved drafts → existing `kb-curator` agent for vault write (one chunk at a time, so kb-curator's per-entry interrupt-on-new-theme behavior still fires)
- [ ] **REV-07**: Rejected drafts logged to `voicenote/state/voicenote.db` with timestamp + reason (button label); audit trail without re-prompting

### Vault Write Path (VAULT)

Cross-boundary refactor that closes existing `[high]` CONCERNS.md bug (kb_curator writes but never commits submodule).

- [ ] **VAULT-01**: Refactor `pipeline/vault_writer.py` — extract `apply_curation_plan` as a shared helper used by both existing pipeline and new voicenote module
- [ ] **VAULT-02**: Add `commit_vault_submodule()` helper that stages + commits new entries through the `obsidian-vault` submodule explicitly; called by both vault writers
- [ ] **VAULT-03**: Frontmatter on every entry includes: `source:` (`telegram://msg/<id>` or `notion://page/<id>`), `parent_note: <id>` (links sibling chunks), `source_excerpt_es: |` (verbatim original Spanish), captured-at timestamp
- [ ] **VAULT-04**: `## Source (ES)` body section renders the verbatim ES quote as a blockquote at the end of each entry
- [ ] **VAULT-05**: Textual overlap flag — cheap Jaccard on 3-grams over title slugs + first paragraph; if match ≥ threshold against existing vault entries, write entry anyway and tag with `[[possible-duplicate-of:<slug>]]`
- [ ] **VAULT-06**: Vault writes serialized via `flock` on `voicenote/state/vault.lock` to prevent race with the existing pipeline writing concurrently

### Operations (OPS)

Match existing pipeline ops patterns (systemd timer, watchdog, structured telemetry).

- [ ] **OPS-01**: `systemd/painforwisdom-voicenote-poll.{service,timer}` user unit, 10-minute interval; mirrors existing `painforwisdom-daily-brief.{service,timer}` shape (Restart=on-failure, StartLimitBurst=3 starts in 2h)
- [ ] **OPS-02**: Bot lifecycle is **short-lived drain** — timer fires → `Application.run_polling()` drains pending updates once → exits. No persistent daemon.
- [ ] **OPS-03**: Watchdog script `scripts/check_voicenote_freshness.sh` (cron-driven) — heal-then-notify on stuck pipeline (match commit `2e21bd9` shape), alerts go to existing daily-summary Telegram channel
- [ ] **OPS-04**: Structured JSONL telemetry using existing `runs.jsonl` layout — stage names `voicenote.transcribe`, `voicenote.split`, `voicenote.translate`, `voicenote.extract`, `voicenote.review`, `voicenote.commit`
- [ ] **OPS-05**: Bounded prompt growth — splitter + translator + extractor prompts have explicit token caps per call (no unbounded growth via error-recovery loops; match recent commit `19e0e6e` discipline)
- [ ] **OPS-06**: Failure mode — transcription / split / translation / extraction failures route to `voicenote/state/dead_letter/` with full context; user notified via Telegram error reply; retry feasible
- [ ] **OPS-07**: `retry.py:_ask_indefinitely` mirror — bound the wait via `MAX_REMINDERS` (close `[med]` CONCERNS.md gap), no unbounded blocking
- [ ] **OPS-08**: Telegram error replies / debug dumps redact PII (no full transcripts in error logs); voice contents stay local (`.ogg` only on disk, never in logs)

### Testing (TEST)

Match existing pipeline test patterns (stdlib `unittest`, `MockTransport`, smoke E2E).

- [ ] **TEST-01**: Unit tests per voicenote node (splitter, translator, extractor-wrapper, source adapters, state, vault writer) using stdlib `unittest`
- [ ] **TEST-02**: `httpx.MockTransport` for Telegram + Notion + Anthropic mocks (match `pipeline/` convention)
- [ ] **TEST-03**: Smoke E2E `tests/smoke_voicenote.sh` drives full pipeline against ≥2 ES fixture transcripts (no real audio in test path); matches `tests/smoke_pipeline.sh` pattern
- [ ] **TEST-04**: Backfill dry-run is the canonical integration test (29-page corpus exercises Notion source + splitter + translator + extractor + overlap flag in one run)

## v2 Requirements

Triggered by friction observed in real use after v1 stabilizes.

### Review UX (REV2)

- **REV2-01**: Per-chunk [Edit] button → `force_reply` text input → re-extract on edited text
- **REV2-02**: [Merge ↑] adjacent-chunks button (collapses chunk N into N-1; re-runs translator + extractor on merged ES text; merge depth ≤ 1, no chains)
- **REV2-03**: [Retry] button on failed long-note (recoverable transient failures)
- **REV2-04**: Suspected-duplicate inline link in Telegram review preview (clickable to existing vault entry)
- **REV2-05**: Theme-level conflict warning when ≥20 entries already exist under target theme

### Capture (CAP2)

- **CAP2-01**: Caption title hint — non-`/N` caption content seeds the splitter's title-suggestion field
- **CAP2-02**: Save-on-timeout — pending drafts > 24 h persist to `voicenote/pending/<note-id>.json` instead of expiring; manual `/list-pending` resume

### Backfill (NTN2)

- **NTN2-01**: Per-subpage status table — Markdown rollup written at end of backfill run (queued / processing / done / failed / skipped-duplicate)
- **NTN2-02**: Bounded parallelism for backfill — `asyncio.Semaphore(3)` around per-note pipeline

### Ops (OPS2)

- **OPS2-01**: Daily/weekly capture summary message — "This week: N notes → M entries → K themes touched"

## Out of Scope

Explicit exclusions. Documented to prevent scope creep.

| Feature | Reason |
|---------|--------|
| Voicepal app/API integration (live) | Subscription being cancelled; only Voicepal touchpoint is the one-shot Notion read |
| Multi-user accounts / public bot | Single-user system; allowlist gates the only user |
| Public web UI / review dashboard | Telegram inline keyboards cover 95% of cases; HTTP server is extra surface |
| Real-time transcription / streaming Whisper | Cron poll 5–15 min is acceptable; streaming adds complexity for no gain |
| Telegram webhook server | Requires public URL + TLS + tunnel; cron-poll `getUpdates` avoids all that |
| Mobile app (RN/Flutter) | Telegram IS the mobile app |
| OpenAI Whisper API / Deepgram / AssemblyAI | Local Whisper acceptable; revisit only if Spanish quality is insufficient |
| Auto-commit without review | Removes the cheap insurance against bad splits/translations |
| Vault PR / git-review per entry | 4 entries × N notes/month = unworkable PR volume; direct commit to `draft` branch with source-ID frontmatter |
| Notion staging DB before vault | Backfill writes straight to vault; review is in Telegram, not Notion |
| Threaded reply chains as multi-message capture | Doubles state model; single-shot per voice message; v2 idea only |
| Text fallback (typed long note via bot) | Existing video pipeline + Notion blog handle typed input; voicenote bot is voice-only |
| Embedding-based dedup | Pinecone/pgvector/Weaviate adds heavy dep; textual overlap + flag-don't-skip is enough for v1 |
| Embedding-based theme suggestion | `kb-curator` already handles theme routing; adding a layer in voicenote duplicates responsibility |
| Multi-language vault output | Vault stays English-uniform; `source_excerpt_es` frontmatter + `## Source (ES)` body preserves ES voice |
| Calendar/meeting integration | Out of domain — voicenote is voluntary reflection, not meeting transcription |
| Sentiment / emotion tagging | Out of domain — vault value is coaching insight, not affect |
| LangGraph DAG for voicenote flow | Linear pipeline doesn't need DAG/`interrupt()`; over-spec. Plain Python + SQLite state. Existing pipeline's LangGraph stays untouched |
| Auto-discard on Telegram timeout | Silent loss is worse than stale drafts; 24h expiry moves to `expired/`, never deletes |
| Auto-commit on Telegram timeout | Defeats review-before-commit |

## Traceability

Which phases cover which requirements. Updated during roadmap creation.

| Requirement | Phase | Status |
|-------------|-------|--------|
| POC-01 | TBD | Pending |
| POC-02 | TBD | Pending |
| POC-03 | TBD | Pending |
| POC-04 | TBD | Pending |
| POC-05 | TBD | Pending |
| CAP-01 | TBD | Pending |
| CAP-02 | TBD | Pending |
| CAP-03 | TBD | Pending |
| CAP-04 | TBD | Pending |
| TG-01 | TBD | Pending |
| TG-02 | TBD | Pending |
| TG-03 | TBD | Pending |
| TG-04 | TBD | Pending |
| TG-05 | TBD | Pending |
| TG-06 | TBD | Pending |
| NTN-01 | TBD | Pending |
| NTN-02 | TBD | Pending |
| NTN-03 | TBD | Pending |
| NTN-04 | TBD | Pending |
| NTN-05 | TBD | Pending |
| PROC-01 | TBD | Pending |
| PROC-02 | TBD | Pending |
| PROC-03 | TBD | Pending |
| PROC-04 | TBD | Pending |
| PROC-05 | TBD | Pending |
| PROC-06 | TBD | Pending |
| PROC-07 | TBD | Pending |
| REV-01 | TBD | Pending |
| REV-02 | TBD | Pending |
| REV-03 | TBD | Pending |
| REV-04 | TBD | Pending |
| REV-05 | TBD | Pending |
| REV-06 | TBD | Pending |
| REV-07 | TBD | Pending |
| VAULT-01 | TBD | Pending |
| VAULT-02 | TBD | Pending |
| VAULT-03 | TBD | Pending |
| VAULT-04 | TBD | Pending |
| VAULT-05 | TBD | Pending |
| VAULT-06 | TBD | Pending |
| OPS-01 | TBD | Pending |
| OPS-02 | TBD | Pending |
| OPS-03 | TBD | Pending |
| OPS-04 | TBD | Pending |
| OPS-05 | TBD | Pending |
| OPS-06 | TBD | Pending |
| OPS-07 | TBD | Pending |
| OPS-08 | TBD | Pending |
| TEST-01 | TBD | Pending |
| TEST-02 | TBD | Pending |
| TEST-03 | TBD | Pending |
| TEST-04 | TBD | Pending |

**Coverage:**
- v1 requirements: 52 total
- Mapped to phases: 0 (will be filled by roadmapper)
- Unmapped: 52 ⚠️ (expected — roadmapper resolves)

---
*Requirements defined: 2026-05-18*
*Last updated: 2026-05-18 after initial definition*
