# Project Research Summary — Voicenote Module

**Project:** Voicenote — long-form Spanish voice-note → English coaching-thought vault entries
**Domain:** Single-user PKM capture surface bolted onto the existing `painforwisdom` LangGraph pipeline; new `voicenote/` Python module sibling to `pipeline/`
**Researched:** 2026-05-18
**Confidence:** HIGH (stack, architecture, pitfalls grounded in existing codebase + verified docs); MEDIUM (Spanish Whisper WER on Gonzalo's actual voice + LLM-split reliability on conversational ES — both validated in PoC phase)

## 1. Headline — What We Learned

Voicenote is structurally simple — five linear stages (transcribe → split → translate → extract → review → commit) with one async pause (Telegram inline-button approval) — but it sits at the intersection of two failure-rich substrates: **Spanish-on-local-Whisper** (silence hallucination, code-switching drift, padding offsets) and **Telegram bot HITL** (update_id persistence, 20 MB getFile cap, CallbackQuery 15 s timeout, draft-state durability across cron ticks). The cross-cutting design tension — preserve Gonzalo's signature ES voice through to an EN vault — is resolved by the locked **transcribe → split (ES) → translate per chunk** ordering AND by retaining the original ES verbatim in each entry's frontmatter + `## Source (ES)` block. The most expensive risks (Spanish Whisper unusable; LLM split unreliable) are also the riskiest assumptions, so the build order is PoC-first per `feedback_poc_before_migration` — validate transcript quality + split quality on real fixtures **before** writing any module scaffolding.

Stack-wise the answer is **maximum reuse, exactly one new dep**: `python-telegram-bot==22.7` (verified absent from current `requirements.txt`; the existing `telegram_io.sh` curl wrapper cannot support voice-file download + inline keyboards + CallbackQuery). Everything else is reused — `pipeline.llm.call_llm` for split/translate/extract, `extract_transcription.sh` for Whisper (bumped to `large-v3` for ES voicenote runs only, `medium` stays the video default), `pipeline.notion_client` for backfill, `pipeline.runtime.append_metric` for telemetry. Architecturally **plain Python, NOT LangGraph** — the flow is linear and the only async pause is naturally idempotent via persisted draft state in SQLite. Scheduling matches the existing `painforwisdom-daily-brief.timer` exactly: short-lived oneshot via systemd user timer + cron watchdog.

The single biggest **inherited scar** is the dirty `obsidian-vault` submodule (kb_curator writes but never commits — `[high]` in CONCERNS.md). Voicenote forces resolution: Phase 3 lifts `kb_curator._apply_proceed` into `pipeline/vault_writer.py:apply_curation_plan` and adds `commit_vault_submodule()`. Both pipelines share one vault-write path going forward.

## 2. Stack — Final Picks

See `.planning/research/STACK.md` for full rationale + alternatives.

| Component | Pick | Rationale |
|---|---|---|
| **Telegram bot** | **`python-telegram-bot==22.7`** (NEW, only new dep) | `Bot.get_updates(offset=..., timeout=0)` for one-shot polling + `MessageHandler(filters.VOICE)` + `CallbackQueryHandler` + `ConversationHandler` for review-edit flow. Current `telegram_io.sh` (curl wrapper) cannot support voice file download or CallbackQuery. Async-only since v20; worker uses `asyncio.run()` for the Telegram leg only. |
| **Spanish transcription** | **Local Whisper `large-v3`** via existing `extract_transcription.sh` with `WHISPER_MODEL=large-v3 LANGUAGE=Spanish` env override | No new pip dep. WER ~5-7 % vs `medium` ~9-11 % on conversational ES. `medium` stays the video default (already calibrated for confidence gates). `large-v3-turbo` preferred if conda env has weights (Phase 0 verification task). CPU fallback acceptable (~25-35 min on 10-min audio — fits 10-min cron cadence). `--initial_prompt` carries a 30-term ES↔EN code-switch glossary to prevent stuck-in-English drift. |
| **LLM split (ES → atomic chunks)** | `pipeline.llm.call_llm` + new prompt `.claude/agents/voicenote-splitter.md` with `tool_use` structured output | Anthropic Sonnet 4.6 via LiteLLM (already wired, inherits OAuth rotation + cost telemetry + 1M-context gating). Splits on Spanish text BEFORE translation — preserves nuance per locked decision. No LangChain text-splitters. |
| **Translation (per chunk ES→EN)** | `pipeline.llm.call_llm` + few-shot voice examples from vault + `preserve_verbatim` glossary | Same Sonnet 4.6 path. No DeepL, no MarianMT. Per-chunk only (never round-trip). Cache by `sha256(transcript)`. |
| **Coaching-thought extraction** | Reuse existing `coaching-thought-extractor.md` agent prompt | Per-chunk extraction; fork the prompt to a `coaching-thought-extractor-chunk.md` variant only if PoC shows chunk-distribution breaks the existing prompt. |
| **Vault writer** | Refactor `pipeline/nodes/kb_curator.py:_apply_proceed` → `pipeline/vault_writer.py:apply_curation_plan(plan, *, parent_note, source_uri, overlap_flag)` + new `commit_vault_submodule()` helper | One cross-boundary touch into `pipeline/`. Closes CONCERNS.md `[high]` (dirty vault submodule). |
| **Scheduling** | NEW systemd user unit `painforwisdom-voicenote.{service,timer}` at 5-10 min cadence (`OnCalendar=*:0/10`) | Matches existing `painforwisdom-daily-brief` pattern exactly. `Restart=on-failure RestartSec=300 StartLimitBurst=3 StartLimitIntervalSec=2h`. `Type=oneshot` is naturally non-overlapping. NOT cron, NOT a persistent daemon. |
| **State persistence** | SQLite at `voicenote/state/voicenote.db` — tables: `long_notes`, `drafts`, `telegram_cursor`, `processed_notion_pages` | One file, stdlib `sqlite3` (no ORM, matches `pipeline/themes_db.py`). Transactional status transitions. Single source of truth across cron ticks + replay CLI. |
| **Pipeline shape** | **Plain Python, NOT LangGraph** (linear stages, one async pause, SQLite is the resume primitive) | Avoids LangGraph DAG + SqliteSaver duplication. Existing LangGraph pipeline stays untouched. |
| **Audio retention** | Filesystem-only at `voicenote/audio/YYYY-MM-DD-<slug>.ogg`, gitignored, indexed via entry frontmatter | No new audio lib (no `mutagen`/`pydub`/`tinytag`). |
| **Watchdog** | Cron-driven `pipeline/scripts/check_voicenote_freshness.sh`, heal-then-notify pattern, dedupe state under `~/.local/state/painforwisdom/` | Matches the May 2026 daily-brief watchdog hardening exactly (commit `2e21bd9`). |

**Dependencies that are NOT being added** (deliberate, per single-new-dep constraint): LangChain, faster-whisper, whisperx, APScheduler, aiogram, pyTelegramBotAPI, Pydantic, sqlmodel/sqlalchemy/peewee, pydub/mutagen/tinytag, DeepL/MarianMT/any translation library.

## 3. Table-Stakes vs Differentiators vs Anti-Features

Full landscape in `.planning/research/FEATURES.md`. Voicenote-specific only (capabilities reused from `pipeline/` not enumerated).

### Table Stakes (v1 launch — system is broken without these)

- Long Telegram voice ingest (poll, not webhook) + user-id allowlist (silent reject, closes `_wait_reply` chat-id gap)
- Spanish-source preservation through split: transcribe → split (ES) → translate per chunk
- LLM auto-split with caption `/N` override; per-chunk ES→EN translation
- Review-before-commit via inline keyboards (Approve / Reject for v1; Edit/Merge deferred to v1.x)
- Source + `parent_note` frontmatter on every entry; ES verbatim in `source_es:` + `## Source (ES)` body section
- Audio retention (`.ogg` per entry, gitignored)
- Notion backfill one-shot for 29 Voicepal subpages (early in Phase 2; same `LongNote` adapter as Telegram)
- Backfill idempotency (`processed_notion_pages`) + dry-run mode
- Telegram poll cursor (`update_id` persisted to SQLite, written BEFORE Whisper runs)
- Textual overlap flag (`[[possible-duplicate-of:<slug>]]`) — flag-don't-skip
- Structured JSONL telemetry matching `pipeline/runs.jsonl` conventions
- Telegram success/failure notifications

### Differentiators (v1.x — once daily loop is stable)

- Per-chunk Edit button (`ConversationHandler` text-reply flow)
- Merge-adjacent-chunks button (`Merge ↑`, depth ≤ 1)
- Per-chunk translation-confidence flag (⚠️ on code-switched chunks)
- Suspected-duplicate inline link in review prompt
- Theme-saturation warning (≥20 entries in a theme)
- Caption hints (`/3` for split count, freeform for title seed)
- Save-draft-on-Telegram-timeout (avoid silent draft loss)
- Retry button on failed notes
- Bounded parallelism for backfill (`Semaphore(2-3)`)
- Daily/weekly capture summary

### Anti-Features (NOT building — would break locked decisions or single-user reality)

- Multi-user accounts / public bot
- Public web UI / dashboard
- Hosted vector DB for dedup (embedding-based dedup deferred indefinitely)
- Real-time transcription / webhook server
- Mobile-app wrapper
- Cloud transcription vendors (OpenAI Whisper API, Deepgram, AssemblyAI)
- Auto-commit without review
- Vault PR / git-review per entry
- Notion staging DB before vault
- Threaded reply chains as multi-message capture
- Text-message fallback for typed long notes
- Sentiment/emotion tagging
- Multi-language vault output (ES preserved in frontmatter only)
- Auto-discard or auto-commit on Telegram timeout

## 4. Architecture — Module Layout, State, Build Order

Full design in `.planning/research/ARCHITECTURE.md`.

### Module layout (sibling to `pipeline/`)

```
voicenote/
├── __main__.py + cli.py                 # `python -m voicenote {poll-once,backfill,status,replay}`
├── worker.py                            # one-shot orchestrator called by systemd timer
├── state/voicenote.db                   # SQLite — long_notes, drafts, telegram_cursor, processed_notion_pages
├── state/audio/                         # retained .ogg (gitignored)
├── state/dead_letter/                   # FAILED notes for replay (gitignored)
├── sources/{telegram.py,notion.py}      # Source protocol: iter_pending() -> Iterator[LongNote]
├── processing/{transcribe,split,translate,extract}.py
├── review/{presenter,handler}.py        # Telegram inline-keyboard UI
├── vault/writer.py                      # wraps pipeline.vault_writer.apply_curation_plan + commit_vault_submodule
├── repo.py                              # SQLite DAL (only file that opens the connection)
├── models.py                            # @dataclass LongNote, Draft, NoteStatus enum
├── allowlist.py                         # user_id env gate
└── tests/                               # stdlib unittest mirrors tests/ conventions
```

### State machine (lives in SQLite, single source of truth)

```
PENDING → TRANSCRIBING → TRANSCRIBED → SPLITTING → SPLIT
       → TRANSLATING → TRANSLATED → EXTRACTING → EXTRACTED
       → AWAITING_REVIEW  ──┬→ APPROVED → COMMITTING → COMMITTED
                            └→ REJECTED (terminal)
                            ↓ (any failure)
                          FAILED (replayable via voicenote/state/dead_letter/)
```

Decision granularity is **per-draft, not per-note** — a 4-chunk note can land 3 approved + 1 rejected; the rejected draft stays in the DB for audit, never reaches the vault.

### Async pause is naturally cron-friendly

Worker exits at `AWAITING_REVIEW` (rc=0). Next cron tick (a) polls Telegram for `callback_query` updates, (b) updates `drafts.decision` in SQLite, (c) scans `long_notes WHERE status='AWAITING_REVIEW'`, (d) for any note where all drafts are decided, advances to `APPROVED`/`REJECTED` and runs the commit. No event loop, no `interrupt()`, no SqliteSaver. Watchdog = "any row stuck in `*_ING` for >1 hr".

### Build order (PoC-FIRST per `feedback_poc_before_migration`)

1. **PoC Phase 0 — validate the two HIGH-risk assumptions (≤ 1 week, slim, no module scaffolding):**
   - **P0.1** Spanish Whisper quality on a real 10-min ES recording. `extract_transcription.sh --lang Spanish --model large-v3` (and `large-v3-turbo` if available). Manual WER sample. Hard go/no-go.
   - **P0.2** LLM split prompt on the same transcript via `pipeline.llm.call_llm` + a draft `voicenote-splitter.md`. Hand-judge atomicity. Hard go/no-go.
   - **P0.3** Hand-drive translate + existing `coaching-thought-extractor` per chunk. Compare merged output to a vault entry the user would have written manually. Hard go/no-go.
   - **P0.4** Voicepal integration inventory + sub-cancel kill list to OPERATIONS.md (Pitfall 16 — must land BEFORE any new pipeline write).
   - **P0.5** Extend `pipeline.cost_forecast` with `--voicenote` mode (Pitfall 15 + `feedback_cost_forecast_before_replay`).
2. **Build Phase 1 — Telegram capture loop** (`models.py`, `repo.py`, SQLite schema, `sources/telegram.py`, `allowlist.py`, cursor persistence, `worker.py` minimal end-to-end without review).
3. **Build Phase 2 — Notion backfill source + dry-run** (`sources/notion.py`, `processed_notion_pages`, `cli.py backfill --source notion --limit 5 --dry-run`). Early on purpose: 29 subpages are a natural test corpus for the splitter prompt + validates dual-source abstraction before Telegram review complexity.
4. **Build Phase 3 — Review state machine** (`review/presenter.py`, `review/handler.py`, drafts table, inline-keyboard approve/reject + answer-callback-first pattern).
5. **Build Phase 4 — Vault hand-off** (refactor `kb_curator._apply_proceed` → `pipeline/vault_writer.py:apply_curation_plan`; new `commit_vault_submodule()`; `voicenote/vault/writer.py`; flock on vault). Closes CONCERNS.md `[high]`.
6. **Build Phase 5 — Ops + hardening** (systemd unit, cron watchdog, status CLI, dead-letter recovery, redaction helper, theme cap enforcement, semantic theme-dedup).
7. **Build Phase 6 — v1.x differentiators** (Edit/Merge buttons, confidence flags, save-on-timeout, etc.) — only as friction surfaces in real use.

## 5. Critical Pitfalls — Severity-Tagged, Phase-Mapped

Full pitfall catalog in `.planning/research/PITFALLS.md` (16 items). Top items grouped by severity below; each shows prevention summary + the phase that MUST land the mitigation.

### HIGH severity (must prevent — system-breaking or silent-quality-degrading)

| # | Pitfall | Prevention | Phase |
|---|---|---|---|
| 1 | Whisper Spanish silence hallucination ("Gracias por ver…", repeated phrases) + padding offset | Pre-VAD trim (ffmpeg `silenceremove`); force `--language es`; reuse confidence gates from `pipeline/nodes/transcribe.py`; post-Whisper boilerplate blocklist regex; resample to 16 kHz mono | P0 + Phase 1 |
| 2 | Whisper code-switching stuck in English after a code-switched word | Hard-pin `--language es` (never auto); `--initial_prompt` with 30-term code-switch glossary; upgrade to `large-v3` for voicenote | P0 + Phase 1 |
| 3 | Long-transcript chunking splits one coaching idea across calls; under/over-segmentation | Semantic-boundary chunking via LLM (not fixed-window); cap per-chunk ≤ 800 words; 120-word overlap; loud-fail >1200 words; preamble context propagation `<previous_context>` block | P0 + Phase 2 |
| 4 | ES→EN translation flattens narrator's signature voice | Locked decision: split ES first, translate per chunk; `preserve_verbatim` glossary in prompt; 3-5 few-shot examples from user's own prior bilingual entries; NEVER round-trip; cache by `sha256(transcript)` | P0 + Phase 1 |
| 5 | Telegram `update_id` offset persistence loss → re-processes every voice note on restart | SQLite `telegram_cursor` table; write offset BEFORE acknowledging note as processed; idempotency key on `(chat_id, message_id)`; check BEFORE Whisper | Phase 1 |
| 8 | Long-lived pending-draft state lost on process restart | SQLite `drafts` table is single source of truth; `editMessageReplyMarkup` to disable buttons after click; 24-h draft expiry with Telegram nudge; crash-recovery scans `AWAITING_REVIEW` on startup | Phase 3 |
| 9 | Vault submodule write race (existing `pipeline/` + voicenote concurrent) | `flock(2)` on `obsidian-vault/.kb-write.lock`; atomic per-file `write_text` + `os.rename`; auto-commit on every mutation (closes CONCERNS.md `[high]`); funnel ALL vault writes through one node | Phase 4 |
| 10 | `coaching-thought-extractor` assumptions break on 200-word translated chunks | Add `<input_kind>chunk_n_of_m</input_kind>` block; chunk-merge step before `kb_curator` sees the output; fixture-driven validation BEFORE reuse; version-pin the prompt; fork to `coaching-thought-extractor-chunk.md` if needed | Phase 2 |
| 11 | kb_curator theme proliferation (11 → 30 themes under voicenote volume) | Hard cap ≤ 14 themes in `kb-curator.md`; semantic-dedup pre-check (cosine vs existing); per-month theme-introduction quota with HITL yellow-flag; weekly consolidation report | Phase 4 / Phase 5 |
| 14 | Privacy leak — transcript prose in logs, Telegram errors, error-recovery prompts back to Anthropic | `_redact_transcript()` helper in error paths; Telegram errors carry run_id + stage only; `.gitignore` audit; 30-day `.ogg` retention sweep; never log full transcripts | Phase 1 |
| 15 | LLM quota burn — voicenote × N stages × retry storms × Sonnet pricing | `pipeline.cost_forecast --voicenote` mode; pre-run quota gate vs `PRO_MAX5_SONNET_MSGS_PER_5H`; defer + Telegram on >80 %; bound retry counts; mirror `_ask_bounded` into `pipeline/retry.py:_resume_graph` BEFORE rollout | P0 + Phase 5 |

### MEDIUM severity (loud-fail required; not system-breaking but corrosive)

| # | Pitfall | Prevention | Phase |
|---|---|---|---|
| 6 | Telegram `getFile` 20 MB cap (asymmetric vs 50 MB send) | Pre-flight `file_size` check on update payload (threshold 19.5 MB); Telegram reply with remediation; idempotency-mark | Phase 1 |
| 7 | CallbackQuery 15-s `answerCallbackQuery` timeout (spinner hangs) | Answer-first pattern: ack callback in <100 ms before any LLM work; LLM in worker on next tick; track 48-h `editMessageText` window | Phase 3 |
| 12 | Notion backfill rate-limit + recursive children + pagination | `tenacity` exponential backoff reading `Retry-After`; centralised pacer (closes CONCERNS.md `[med]`); loop on `has_more`/`next_cursor`; recursion cap depth 8; idempotency on `(page_id, last_edited_time)` | Phase 2 |
| 13 | Cron + systemd-unit overlap → double-processing | `flock -n` on every cron entry OR systemd `Type=oneshot` + timer (preferred — naturally non-overlapping); never mix cron and systemd for same task | Phase 5 |
| 16 | Voicepal residual integration after sub cancellation | Phase 0 inventory checklist; 7-day no-op observation week; tag new entries `Created by: kb-curator (voicenote pipeline)`; one-time audit post-kill | Phase 0 |

### Inherited scars (CONCERNS.md — must be addressed because voicenote compounds them)

- **`[high]` Vault submodule dirty** (`kb_curator` writes, never commits) — voicenote forces fix in Phase 4 (`commit_vault_submodule()`).
- **`[high]` 8 of 11 pipeline nodes have zero tests** (kb_curator, validator the worst) — voicenote's per-chunk extractor needs fixtures, drives test discipline.
- **`[high]` `extract_transcription.sh` stderr not captured into RuntimeError** — voicenote Phase 1 fixes: pass proc.stderr tail into the exception message.
- **`[med]` `_wait_reply` no chat-id filter** — closed by voicenote allowlist (separate bot, separate chat, hard `user_id` check).
- **`[med]` Telegram delivery degrades silently on parse-mode HTML errors** — voicenote uses HTML, must inherit / extend the `_esc()` discipline; bot-ping at worker startup; if Telegram down twice → exit rc=1 before burning LLM tokens.
- **`[med]` `pipeline/retry.py:_resume_graph` uses `_ask_indefinitely`** — fix BEFORE voicenote rollout (Pitfall 15 compounds otherwise).
- **`[med]` 1M-context beta gating** — voicenote splitter inputs are 1200-3500 tokens, well under the cap; ensure `long_context=False` (default) is preserved.

## 6. Reuse-vs-Build Matrix

| Component | Reuse As-Is | Refactor (light) | Build New |
|---|---|---|---|
| `extract_transcription.sh` Whisper invocation | ✓ (with `WHISPER_MODEL=large-v3 LANGUAGE=Spanish` env override) | | |
| `pipeline.llm.call_llm` (LLM wrapper + retry + auth refresh + cost telemetry + 1M gating) | ✓ | | |
| `pipeline.runtime.load_agent_prompt` | ✓ | | |
| `pipeline.runtime.append_metric` (`runs.jsonl` JSONL telemetry) | ✓ | | |
| `pipeline.notion_client` (REST + pacing + cache) | ✓ for backfill reads | Centralise pacing constant; add 429 + `Retry-After` exponential backoff (closes CONCERNS.md `[med]`) | |
| `.claude/agents/coaching-thought-extractor.md` | ✓ initially | Add `<input_kind>chunk_n_of_m</input_kind>` block; fixture-validate on chunk distribution; fork if needed | |
| `.claude/agents/kb-curator.md` | ✓ initially | Add hard theme cap clause (≤14) + semantic-dedup pre-check guidance | |
| `pipeline.telegram.send` (outbound text) | ✓ | Plumb new `TELEGRAM_VOICENOTE_CHAT_ID` env; HTML escape discipline | |
| `pipeline.nodes.kb_curator._apply_proceed` (vault entry write) | | Lift into `pipeline/vault_writer.py:apply_curation_plan(plan, *, parent_note, source_uri, overlap_flag)` — one cross-boundary touch | |
| Vault submodule commit | | | ✓ `commit_vault_submodule(entry_paths, parent_note_id)` helper — closes CONCERNS.md `[high]` |
| systemd-user-timer + cron-watchdog ops pattern | Pattern from `painforwisdom-daily-brief.{service,timer}` | Adapt unit names, paths, watchdog cadence | ✓ new `painforwisdom-voicenote.{service,timer}` + `check_voicenote_freshness.sh` |
| `pipeline/retry.py:_resume_graph` | | Mirror `_ask_bounded` reminder budget (close CONCERNS.md `[med]` before voicenote rollout) | |
| `pipeline.cost_forecast` | | Extend with `--voicenote` mode | |
| `python-telegram-bot==22.7` `Bot` class (one-shot polling, file download, inline keyboards) | | | ✓ ONE new pip dep |
| `voicenote/sources/{telegram,notion}.py` (`Source` Protocol + `Iterator[LongNote]`) | | | ✓ |
| `voicenote/processing/{transcribe,split,translate,extract}.py` (plain functions per stage) | | | ✓ |
| `voicenote/review/{presenter,handler}.py` (inline-keyboard UI + callback handler) | | | ✓ |
| `voicenote/models.py` + `voicenote/repo.py` + SQLite schema | | | ✓ |
| `voicenote-splitter.md` agent prompt | | | ✓ |
| Translation agent (prompt OR inline system message — TBD in Phase 1) | | | ✓ |
| `_redact_transcript()` helper | | | ✓ (Phase 1) |
| 30-term ES↔EN code-switch glossary file | | | ✓ (Phase 1) |
| Boilerplate blocklist regex (post-Whisper) | | | ✓ (Phase 1) |
| stdlib `unittest` tests for new modules | Conventions from `tests/` | | ✓ |

## 7. Suggested Phase Breakdown — Concrete, Ordered, Riskiest-Assumption-First

Each phase below has a single riskiest assumption it de-risks. Order is fixed by dependencies + the PoC-first rule.

### Phase 0 — PoC + Pre-Flight (≤ 1 week, NO module scaffolding yet)

**Riskiest assumption:** Spanish Whisper at `large-v3` produces transcripts good enough that a downstream LLM splitter can find coaching-thought boundaries reliably, AND per-chunk translate-then-extract produces book-grade output indistinguishable from existing video-derived entries.

**Delivers:**
- `large-v3` (or `-turbo`) Whisper run on a real 10-min ES voice note; manual WER sample on 1-2 paragraphs; CPU vs GPU timing.
- Draft `.claude/agents/voicenote-splitter.md` with `tool_use` schema. Run on the PoC transcript; hand-judge chunk atomicity.
- Hand-driven translate + `coaching-thought-extractor` per chunk via `pipeline.llm.call_llm`. Compare merged output to a manually-curated vault entry.
- **GO/NO-GO gate:** if any of (transcript quality | split atomicity | extraction quality) fails, PROJECT.md is updated, architecture pivots, NO module scaffolding starts.
- Voicepal integration inventory (Notion, Obsidian, Calendar, email, webhooks); kill-list in OPERATIONS.md; 7-day no-op observation week BEFORE sub-cancel.
- `pipeline.cost_forecast --voicenote` mode that projects per-note tokens (boundary + translation + extraction × N + merge + kb_curator) and quota share against `PRO_MAX5_SONNET_MSGS_PER_5H`.
- `pipeline/retry.py:_resume_graph` switched from `_ask_indefinitely` → `_ask_bounded` (closes CONCERNS.md `[med]` BEFORE voicenote multiplies retry surface).

**Pitfalls addressed:** 1, 2, 3, 4, 15, 16.
**Research flag:** Phase-specific research likely needed on (a) `large-v3-turbo` weight availability in current conda env; (b) `coaching-thought-extractor` behavior on chunk-shape input — may need `coaching-thought-extractor-chunk.md` fork.

### Phase 1 — Telegram Capture Loop (minimum end-to-end intake without review)

**Riskiest assumption:** Telegram poll cursor + idempotency + voice-file download + privacy redaction together produce a reliable, restart-safe intake that NEVER re-processes a voice note and never leaks transcript content.

**Delivers:**
- `voicenote/models.py` + `voicenote/repo.py` + SQLite schema (long_notes, telegram_cursor, drafts skeleton).
- `voicenote/sources/telegram.py`: `Bot.get_updates(offset=..., timeout=0)`, voice download via `get_file().download_to_drive()`, `INSERT OR IGNORE` on `(source_kind, source_ref)`, cursor advance AFTER batch, allow-list silent reject.
- `voicenote/allowlist.py` (`VOICENOTE_ALLOWED_USER_ID` env, silent reject pattern).
- `voicenote/processing/transcribe.py`: shell `extract_transcription.sh --lang Spanish --model large-v3` with CUDA→CPU fallback, `flock` on `.whisper.lock`, pre-VAD silence trim, post-Whisper boilerplate blocklist regex, 16 kHz mono resample assertion.
- `voicenote/processing/split.py` + `voicenote/processing/translate.py` + `voicenote/processing/extract.py` (all stages, no review — outputs to disk).
- `_redact_transcript()` helper used by every Telegram-egress error path; `runs.jsonl` writer redacts; redaction unit-tested.
- `voicenote/worker.py` minimal: drain → transcribe → split → translate → extract → write extraction reports to disk → Telegram notification.
- Smoke test: send a real voice message, see entries land as JSON in `voicenote/state/`.

**Pitfalls addressed:** 1, 2, 5, 6, 14.
**Features (table-stakes):** Long voice ingest, user-id allowlist, ES preservation, source frontmatter, audio retention, telegram poll cursor, structured telemetry, notifications.
**Research flag:** Light. PTB v22 patterns are well-documented (Context7 HIGH confidence in STACK.md).

### Phase 2 — Notion Backfill Source + Dry-Run (early on purpose)

**Riskiest assumption:** The `Source` Protocol + `LongNote` abstraction holds across both Telegram (audio + transcribe) and Notion (text-only, skip transcribe) without conditional branching in `processing/`.

**Delivers:**
- `voicenote/sources/notion.py`: list 29 subpages under Voicepal parent `35b5901b-efa9-80d3-bb58-c1a5fc1ce7b3`, `INSERT OR IGNORE` on `(source_kind="notion", source_ref=page_id)`, pacing via reused constant, `processed_notion_pages` idempotency.
- Notion centralised pacing module (closes CONCERNS.md `[med]`); `tenacity` exponential backoff on 429 reading `Retry-After`.
- `voicenote/cli.py backfill --source notion --limit 5 --dry-run`: same pipeline, terminal sink is markdown dump to `voicenote/dryrun/<run_id>/` instead of kb_curator.
- Per-subpage status table written at end of run (JSONL + markdown rollup).
- Apply mode (`--apply`) with cost-forecast pre-flight gate.

**Pitfalls addressed:** 12 (Notion rate-limit + pagination + recursion), 10 (chunk pipeline validated against 29 real ES transcripts before Telegram review complexity).
**Features (table-stakes):** Notion backfill, backfill idempotency, dry-run mode, `LongNote` adapter abstraction.
**Research flag:** Light — `pipeline.notion_client` is well-known territory.

### Phase 3 — Review State Machine (Telegram inline-keyboard HITL)

**Riskiest assumption:** Per-draft inline-keyboard approval — with answer-callback-first, persisted draft state in SQLite, 24-h expiry, and cron-driven async resume — produces a UX that survives process restart, multi-hour user delays, and the 48-h `editMessageText` window without losing drafts or double-processing.

**Delivers:**
- `voicenote/review/presenter.py`: formats draft entries → Telegram messages with `InlineKeyboardMarkup`; records `telegram_message_id` per draft.
- `voicenote/review/handler.py`: `callback_query` dispatch with `pattern=r"^draft:"`; **answer-callback-first** (<100 ms ack before any DB or LLM work); `UPDATE drafts SET decision=:d WHERE id=:id AND decision='pending'` (double-click safety); `editMessageReplyMarkup` to disable buttons.
- 24-h draft expiry; Telegram nudge at 20 h; expired drafts → `voicenote/state/dead_letter/`.
- Crash-recovery on worker start: scan `AWAITING_REVIEW` notes; advance to APPROVED/REJECTED if all drafts decided.
- Per-draft decision granularity (3-of-4 approve is valid).

**Pitfalls addressed:** 7 (CallbackQuery 15 s), 8 (pending-draft durability).
**Features (table-stakes):** Review-before-commit (Approve/Reject); inline keyboards.
**Research flag:** None — covered by `python-telegram-bot` v22 docs + Pitfalls research.

### Phase 4 — Vault Hand-Off (one cross-boundary refactor)

**Riskiest assumption:** Refactoring `kb_curator._apply_proceed` → shared `pipeline/vault_writer.py:apply_curation_plan` does not regress existing video-pipeline behavior, AND adding `commit_vault_submodule()` finally closes the dirty-submodule scar without breaking the existing kb_curator HITL flow.

**Delivers:**
- `pipeline/vault_writer.py:apply_curation_plan(plan, *, parent_note=None, source_uri=None, overlap_flag=None)` — lift-and-shift of `kb_curator._apply_proceed` with new optional kwargs.
- `pipeline/vault_writer.py:commit_vault_submodule(entry_paths, parent_note_id)` — `git add` + `git commit` inside `obsidian-vault/`; **defer push to manual step** for now (push gate per anti-pattern #5 in ARCHITECTURE.md).
- `voicenote/vault/writer.py` wires approved drafts → `apply_curation_plan` → `commit_vault_submodule` → `flock` on `obsidian-vault/.kb-write.lock`.
- Existing `pipeline/nodes/kb_curator.py` updated to call the shared helper (no behavior change; verified via existing video smoke).
- Validator extended: confirm `VAULT_ROOT/.git` exists; check for dirty submodule post-run.

**Pitfalls addressed:** 9 (vault write race), CONCERNS.md `[high]` (dirty submodule), CONCERNS.md `[med]` (parallel-branch state-key discipline preserved).
**Features (table-stakes):** Source + `parent_note` frontmatter; ES verbatim retention in entry body.
**Research flag:** None — pure refactor + lock primitive.

### Phase 5 — Ops + Hardening (production-ready single-user system)

**Riskiest assumption:** systemd + cron watchdog + theme-cap enforcement + dead-letter recovery + cost-forecast gate produce an unattended operations posture that matches daily-brief's hardening level.

**Delivers:**
- `~/.config/systemd/user/painforwisdom-voicenote.{service,timer}` (`OnCalendar=*:0/10`, `Type=oneshot`, retry budget mirroring daily-brief).
- `pipeline/scripts/check_voicenote_freshness.sh` cron watchdog with heal-then-notify; dedupe state at `~/.local/state/painforwisdom/voicenote_watchdog.last_alert`.
- Theme cap (≤14) enforced in `kb-curator.md`; semantic-dedup pre-check via embedding cosine vs existing themes (≥0.85 → "reuse?").
- Per-month theme-introduction quota flag.
- Weekly theme-consolidation report (Telegram).
- 30-day `.ogg` retention sweep cron.
- Dead-letter replay CLI: `python -m voicenote replay --id <note_id>` re-runs from `FAILED` state.
- `python -m voicenote status` CLI for human inspection of the state machine.
- Pre-run cost-forecast gate enforced in worker startup.

**Pitfalls addressed:** 11 (theme proliferation), 13 (cron overlap), 14 (retention), 15 (quota burn at runtime).
**Features (table-stakes):** Telegram poll cadence + watchdog; structured telemetry rollups.
**Research flag:** Light — operations pattern already proven by daily-brief.

### Phase 6 — Differentiators (only as friction surfaces)

**Riskiest assumption:** Each P2 feature is worth its implementation cost; surface as friction is observed in real use (do NOT pre-build).

**Delivers (each ships independently as triggered):**
- Per-chunk Edit button (`ConversationHandler` text-reply edit flow).
- Merge-adjacent-chunks button.
- Per-chunk translation-confidence flag (⚠️).
- Suspected-duplicate inline link in review prompt.
- Theme-saturation warning.
- Caption hints (`/N`, title seed).
- Save-draft-on-Telegram-timeout.
- Retry button on failed notes.
- Bounded parallelism for backfill (only if Phase 2 backfill runs too slow).
- Daily/weekly capture summary.

**Pitfalls addressed:** None new; each feature inherits its phase-resident mitigations.
**Research flag:** None — well-understood scope.

### Phase ordering rationale

- **PoC-first (Phase 0)** is non-negotiable per `feedback_poc_before_migration` — the two HIGH-risk technical assumptions (Spanish Whisper quality + LLM split reliability) are also the cheapest to validate (≤ 1 week, no scaffolding).
- **Telegram intake (Phase 1) before review (Phase 3)** because the intake loop alone surfaces 5 HIGH-severity pitfalls (1, 2, 5, 14 plus the no-silent-drop discipline of 15) and produces useful output (extraction reports to disk) without the review-UX complexity.
- **Backfill (Phase 2) early, before review (Phase 3)** because 29 subpages are a natural test corpus for the splitter prompt + validate the `LongNote` dual-source abstraction BEFORE Telegram review's async-pause complexity layers on. Dry-run + idempotency make this safe.
- **Vault hand-off (Phase 4) after review (Phase 3)** because the vault write is the irreversible step; it must come after a working review loop that lets the user veto bad chunks.
- **Ops + hardening (Phase 5) before differentiators (Phase 6)** because watchdog + theme-cap + cost-gate prevent expensive failure modes that differentiators would only paper over.

### Research flags per phase

| Phase | `/gsd-research-phase` needed? | Reason |
|---|---|---|
| Phase 0 (PoC) | Likely YES | `large-v3-turbo` weight availability in current conda env; chunk-shape behavior on `coaching-thought-extractor` may need empirical investigation |
| Phase 1 (intake) | NO | PTB v22 + Whisper subprocess patterns well-documented; STACK.md covers it |
| Phase 2 (backfill) | NO | Notion API patterns + `pipeline.notion_client` well-known |
| Phase 3 (review) | Light | Inline-keyboard answer-first pattern + 48 h `editMessageText` window covered by Pitfalls research |
| Phase 4 (vault) | NO | Pure refactor of existing code |
| Phase 5 (ops) | NO | Mirrors daily-brief — pattern proven |
| Phase 6 (differentiators) | Per-feature, lightweight | Each is a small isolated change |

## 8. Confidence Assessment

| Area | Confidence | Notes |
|------|------------|-------|
| Stack | HIGH | Existing-stack reuse verified against `pipeline/requirements.txt`; PTB v22.7 confirmed latest stable via Context7 + GitHub releases; `large-v3` WER estimates from multiple independent sources (MEDIUM on exact WER for Gonzalo's voice, verified in PoC) |
| Features | MEDIUM-HIGH | Single-user PKM landscape researched against Voicepal/Audionotes/Rambler/Speakwise; locked decisions surface clearly; unknown how much editing friction the inline-keyboard Approve/Reject covers before Edit becomes required (P2 trigger) |
| Architecture | HIGH | Module layout + state-machine grounded in existing `pipeline/` patterns + verified against codebase reads (`pipeline/notion_client.py`, `pipeline/llm.py`, `pipeline/nodes/kb_curator.py`, `pipeline/nodes/transcribe.py`); LangGraph-vs-plain-Python tradeoffs are unambiguous given the linear flow |
| Pitfalls | HIGH | 16 pitfalls, severity-tagged, every one with prevention + warning-signs + phase mapping; sourced from web docs (Telegram Bot API, Whisper issues, Notion rate-limit docs) + the existing CONCERNS.md scar tissue |

**Overall confidence:** HIGH.

### Gaps to address during execution

1. **Empirical Spanish WER on Gonzalo's voice** — `large-v3` is the published baseline best for ES; actual WER on Gonzalo's accent + recording environment is unknown. PoC P0.1 closes this. Fallback if unacceptable: Phase-2 evaluation of `faster-whisper` (CONCERNS-aware: port confidence-gating from bash to Python).
2. **`large-v3-turbo` weight availability** — would be a 5× speedup at same WER. Phase 0 first task verifies; if absent, ship with `large-v3` and revisit.
3. **LLM split prompt reliability on conversational ES** — atomic-chunk recall on 5-15 min monologues with thinking-pauses + side-trails is a novel agent task. PoC P0.2 + iteration on the 29-subpage corpus in Phase 2.
4. **Chunk-distribution behavior of `coaching-thought-extractor.md`** — prompt was tuned for 200-600-word raw transcripts; 200-800-word chunks may need an `<input_kind>chunk_n_of_m</input_kind>` block or a fork. PoC P0.3 surfaces this; fork decision in Phase 1 if needed (per Pitfall 10).
5. **Theme saturation threshold** — the ≥20 entries/theme cutoff for saturation warning is heuristic; tune after a month of voicenote volume.
6. **24-h draft expiry vs Telegram 48-h edit window** — 24 h is safely inside but not yet validated against Gonzalo's actual response cadence; may need adjustment in Phase 6 based on save-on-timeout signal.
7. **Translation prompt: per-chunk vs single-shot with chunk markers** — per-chunk preserves nuance but loses context; single-shot preserves context but is parse-fragile. PoC P0.3 informs; final decision in Phase 1.

## Sources

### Primary (HIGH confidence)

- `/python-telegram-bot/python-telegram-bot` — Context7 (Snippets 659, Reputation High, Benchmark 85.64); verified `Application`, `Bot.get_updates`, `MessageHandler(filters.VOICE)`, `CallbackQueryHandler`, `ConversationHandler`, `get_file().download_to_drive` APIs current at v22.5/22.7
- [python-telegram-bot v22.7 stable docs](https://docs.python-telegram-bot.org/en/stable/index.html)
- [Telegram Bot API official docs](https://core.telegram.org/bots/api) — getUpdates offset semantics, callback_query, getFile 20 MB asymmetry, inline keyboards, 48-h editMessageText window
- [Notion API docs — get-block-children + pagination](https://developers.notion.com/reference/get-block-children)
- `/systran/faster-whisper` — Context7 (Snippets 70, Benchmark 86.84) — informs Phase-2 evaluation only
- In-repo codebase reads (HIGH): `pipeline/requirements.txt`, `pipeline/telegram.py`, `pipeline/llm.py`, `pipeline/notion_client.py`, `pipeline/nodes/kb_curator.py`, `pipeline/nodes/transcribe.py`, `pipeline/runtime.py`, `pipeline/state.py`, `extract_transcription.sh`, `telegram_io.sh`, `~/.config/systemd/user/painforwisdom-daily-brief.{service,timer}`
- `.planning/codebase/CONCERNS.md`, `.planning/codebase/CONVENTIONS.md`, `.planning/codebase/INTEGRATIONS.md`, `.planning/codebase/STRUCTURE.md`, `.planning/codebase/ARCHITECTURE.md` (HIGH — direct file reads)
- User-memory rules (HIGH — explicit user-stated): `user_ultra_subscription`, `pipeline_perf_baseline`, `feedback_poc_before_migration`, `feedback_no_silent_feature_drops`, `feedback_cost_forecast_before_replay`, `feedback_audio_overview_format`, `feedback_vault_vs_notion_themes`, `feedback_drop_catchy_title`

### Secondary (MEDIUM confidence)

- [Whisper Large V3 Turbo: As Good as Large V2 but 6x Faster (Medium, 2024)](https://medium.com/@bnjmn_marie/whisper-large-v3-turbo-as-good-as-large-v2-but-6x-faster-97f0803fa933) — speed/WER comparison
- [adriszmar/whisper-large-v3-turbo-es (HuggingFace)](https://huggingface.co/adriszmar/whisper-large-v3-turbo-es) — ES fine-tune 5.34 % WER
- [Faster Whisper in Transana 5.30 — accuracy + speed (2025-05)](https://www.transana.com/blog/2025/05/01/faster-whisper-in-transana-5-30-accuracy-and-processing-speed-3-of-3/)
- [Whisper hallucination on silence — whisper.cpp #1724](https://github.com/ggml-org/whisper.cpp/issues/1724); [openai/whisper #679](https://github.com/openai/whisper/discussions/679)
- [Multi-Language Audio + Transcription Inconsistencies — openai/whisper #2009](https://github.com/openai/whisper/discussions/2009)
- [Improving LLM Abilities in Idiomatic Translation — arXiv 2407.03518](https://arxiv.org/abs/2407.03518)
- [Long-Form Speech Translation through Segmentation — EMNLP 2023](https://aclanthology.org/2023.findings-emnlp.19.pdf) — supports split-before-translate locked decision
- [Rambler: Supporting Writing With Speech via LLM-Assisted Gist Manipulation — CHI 2024](https://dl.acm.org/doi/10.1145/3613904.3642217) — manual + semantic split/merge UX
- [Chunking Strategies for LLM Applications — Pinecone](https://www.pinecone.io/learn/chunking-strategies/); [Chunking Strategies — Weaviate](https://weaviate.io/blog/chunking-strategies-for-rag)
- [Notion API Rate Limits Are Breaking Your Automation — DEV](https://dev.to/kanta13jp1/notion-api-rate-limits-are-breaking-your-automation-heres-the-real-fix-o5p)
- [tdlib/telegram-bot-api #683 — 50 MB upload / 20 MB download asymmetry](https://github.com/tdlib/telegram-bot-api/issues/683)
- [CallbackQuery 15-s timeout reproduction gist](https://gist.github.com/d-Rickyy-b/f789c75228bf00f572eec4450ed0d7c9)
- [getUpdates Telegram Bot: 5 Proven Fixes — BotHero](https://blog.bothero.ai/getupdates-telegram-bot-the-polling-method-that-powers-43-of-small-business-bots-why-it-breaks-at-scale-and-what-to-do-about-it)
- [Prevent overlapping cron jobs with flock — ma.ttias.be](https://ma.ttias.be/prevent-cronjobs-from-overlapping-in-linux/)
- [Voicepal reviews — Substack actionable notes / navid.me / hardlyhamilton.com](https://actionablenotes.substack.com/p/productivity-tool-review-voicepal) — competitor feature landscape

### Tertiary (LOW confidence — single source or inferred)

- [Audionotes — PKM voice tools landscape](https://www.audionotes.app/blog/best-personal-knowledge-management-tools)
- [Speakwise — Obsidian alternatives 2026](https://speakwiseapp.com/blog/obsidian-alternatives)
- [Best second brain apps 2026 — Atlasworkspace](https://www.atlasworkspace.ai/blog/best-second-brain-apps)
- Theme-saturation threshold (≥20 entries) — heuristic, no external source

---
*Research synthesized: 2026-05-18*
*Ready for roadmap: yes*
