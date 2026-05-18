# Feature Research — Long-Form Voice-Note → Personal Vault Pipeline

**Domain:** Single-user long-form voice capture surface, augmenting an existing LangGraph DAG that already handles transcription, coaching-thought extraction, and KB curation
**Researched:** 2026-05-18
**Confidence:** MEDIUM-HIGH

## Scope Note

This module is a NEW capture surface bolted onto an existing pipeline. Features already provided by `pipeline/` and reused as-is (and therefore NOT enumerated here) include:

- Local Whisper transcription (`extract_transcription.sh`, `pipeline/nodes/transcribe.py`)
- Coaching-thought extraction (`coaching-thought-extractor` agent)
- Vault entry / theme / framework / book-outline maintenance (`kb-curator` agent)
- LangGraph retry policy, SqliteSaver HITL checkpointing
- Notion REST client with rate-pacing (`pipeline/notion_client.py`)
- Telegram I/O primitive (`telegram_io.sh` + `pipeline/telegram.py`)
- Structured JSONL telemetry, watchdog patterns, sandbox/prod profile split

Only features SPECIFIC to long-form voice-note ingestion and dual-source (Telegram + Notion-backfill) capture are scoped below.

## Feature Landscape

### Table Stakes (System Is Broken For Gonzalo Without These)

Features whose absence makes the v1 unusable for a single Spanish-speaking PKM operator.

| Feature | Why Expected | Complexity | Notes |
|---------|--------------|------------|-------|
| **Long voice message ingest (Telegram)** | Core capture surface; the whole point. Telegram voice messages up to ~20 min, file size up to 50 MB via Bot API. | MEDIUM | `python-telegram-bot` voice handler → download `.ogg` (OPUS) → existing Whisper path. Polling, not webhook (per locked decision). |
| **User-id allowlist (single-ID)** | Closes the `_wait_reply` chat-id gap noted in `CONCERNS.md`; single-user reality. | LOW | One env var `VOICENOTE_TELEGRAM_USER_ID`. Silent reject (no reply) on mismatch. |
| **Spanish-source preservation through split** | Locked decision: transcribe → split (ES) → translate per chunk. Translating before splitting flattens nuance. | MEDIUM | Splitter operates on ES transcript. Translator operates on each ES chunk. |
| **LLM-based thought splitting (auto-detect N)** | Long notes contain 2–4 distinct thoughts; manual N would defeat capture flow. | MEDIUM | Single LLM call returns ordered chunks with reasoning. Hand-tune prompt against the 29 Voicepal subpages as fixture set. |
| **Per-chunk translation ES → EN** | Vault is 100% English; existing extractor + curator assume English input. | LOW | Per-chunk LLM call. Preserve source-language quote in entry frontmatter (`source_excerpt_es`). |
| **Review-before-commit via Telegram inline keyboards** | Locked decision. Long-note → 2–4 entries is high-leverage but error-prone. | MEDIUM | Per-chunk: Approve / Edit / Reject + batch Approve-all. Inline keyboard sends callback_query, not chat message — keeps thread clean. |
| **Source frontmatter on every entry** | Locked decision. `source: telegram://msg/<id>` or `source: notion://page/<id>`; `parent_note: <id>` links sibling chunks. | LOW | Pure data; no UI. Enables manual re-trace from vault → audio/page. |
| **Audio retention (`.ogg` per entry)** | Locked decision. Cheap on disk, gitignored, replayable if transcription/extraction goes sideways. | LOW | Path: `voicenote/audio/YYYY-MM-DD-slug.ogg`. Indexed by Telegram message id. |
| **Notion backfill (one-shot, 29 subpages)** | Voicepal sub being cancelled; backfill is the migration off Voicepal. | MEDIUM | Read 29 subpages under `Voicepal pages` parent. Each → `LongNote` → same downstream pipeline. |
| **Backfill idempotency (processed-set)** | Re-runs are inevitable (mid-batch crash, prompt iteration). Must not double-write entries. | LOW | Persist processed Notion page IDs to `voicenote/state/processed.jsonl` or SQLite. Skip if seen. |
| **Telegram poll cursor (`last_update_id`)** | Cron poll model requires durable offset, else messages get re-processed or lost on restart. | LOW | Persist to `voicenote/state/telegram_offset.json`. Standard long-poll `offset` parameter. |
| **Source adapter abstraction (`LongNote`)** | Locked decision. Both sources funnel into one downstream graph. | LOW | TypedDict / dataclass: `{id, source, language, text, audio_path?, captured_at, raw_metadata}`. |
| **Overlap flagging (not skip)** | Locked decision. Notion backfill thoughts overlap existing video-derived entries; losing the second pass loses nuance. | MEDIUM | Cheap textual check (title slug + first-paragraph n-gram). Write entry anyway with `[[possible-duplicate-of:<slug>]]` link. |
| **Structured logging matching `pipeline/` conventions** | `pipeline/` operators already trained on JSONL telemetry; reuse `append_metric` and `runs.jsonl` layout. | LOW | Stage names like `voicenote.transcribe`, `voicenote.split`, `voicenote.translate`, `voicenote.extract`. |
| **Telegram error/success notifications** | Single-user system; user IS ops. Must know when a note failed silently. | LOW | Reuse `telegram.send()`. Route to existing main `TELEGRAM_CHAT_ID` (not daily-summary channel — those concerns stay separated). |

### Differentiators (Meaningfully Improve Daily Capture Experience)

Features that compound usefulness over months of capture. Build after table stakes.

| Feature | Value Proposition | Complexity | Notes |
|---------|-------------------|------------|-------|
| **Caption-as-hint (title override / split count)** | Telegram voice messages can carry a caption. User can type `/3` to force 3 splits, or `pain currency` to seed the title. Removes guesswork on ambiguous notes. | LOW | Parse caption with simple regex: `/<int>` for forced N; remainder as title hint. Fall through to LLM auto-detect if absent. |
| **Per-chunk edit in Telegram (inline)** | When LLM split is 90% right but one chunk's translation has a wrong nuance, edit beats reject + re-record. | MEDIUM | Bot replies with each draft, "Edit" button → `force_reply` text input → user pastes corrected text → kb-curator runs on the edited chunk. |
| **Merge adjacent chunks button** | LLM occasionally over-segments (esp. when speaker pauses mid-thought). One-click `Merge ↑` button collapses chunk N into N-1. | MEDIUM | Requires re-running translator + extractor on merged ES text. Keep merge depth ≤ 1 (no chains). |
| **Per-chunk confidence flag from translator** | Some notes code-switch ES↔EN ("entonces el feedback loop is broken"). Detector flags chunks where source language is mixed so user reviews the translation more carefully. | LOW | Translator LLM returns `confidence: HIGH/MEDIUM/LOW` field. Surface as ⚠️ emoji on Telegram preview. |
| **Suspected-duplicate inline link in Telegram** | When overlap flag fires, the bot's preview shows `Possible duplicate: [<slug>]` so user can decide reject/merge/accept-anyway in one tap. | LOW | Cheap: title slug similarity (Jaccard on 3-grams) against vault `_index.md` titles. No embeddings in v1. |
| **Theme-level conflict warning** | If 3 of 4 new chunks land on `[[deliberate-discomfort]]` (already 26 entries — saturated chapter), bot notes "Theme already heavy — consider whether this is novel." | LOW | Read entry count from `_index.md`; threshold (≥20) → ⚠️ in preview. Informational only, no block. |
| **Dry-run mode for backfill** | One-shot 29-page import is irreversible-ish (entries land in vault commit history). Dry-run renders the would-write entries to `voicenote/dryrun/<run-id>/` for inspection before flipping `--apply`. | LOW | Same pipeline, replace kb-curator write step with markdown dump to a scratch dir. Mirrors existing daily-brief `--dry-run` / `--apply` convention. |
| **Per-subpage status table (backfill)** | Visibility into the 29-page import: queued / processing / done / failed / skipped-duplicate. | LOW | Plain JSONL state file + a Markdown rollup written at end of run. Reuses existing telemetry pattern. |
| **Bounded parallelism for backfill** | 29 subpages × (transcribe + split + N × translate + N × extract) sequentially is hours. Parallelism = 2–3 is safe within Anthropic rate-limit + Notion 3 req/s. | LOW | `asyncio.Semaphore(3)` or a simple thread pool around the per-note pipeline. Bound parallelism = 1 in v1 if uncertain — capture user IS gonzalo, no urgency. |
| **Retry button on failed Telegram note** | Recoverable failures (LLM transient, Whisper hiccup) shouldn't require user to re-send. Bot pings "Retry?" button on persistent failure. | LOW | Reuse existing `_drive_graph` retry / abort pattern. Inline button instead of typed reply. |
| **Daily/weekly capture summary** | "This week: 12 notes → 31 entries → 4 themes touched." Mirrors existing daily-brief discipline; pairs well with the writing habit. | LOW | Roll up `voicenote/state/*.jsonl` at fixed time. Telegram message. Optional. |
| **Title hint at top of Telegram preview** | LLM's proposed entry title is what user sees first in preview; if wrong, the rest of the chunk reads wrong. Show title in bold at top of each chunk preview. | LOW | Trivial formatting choice. High-leverage clarity gain. |
| **Save draft on Telegram timeout** | If user doesn't respond to review prompt within N hours, persist draft to `voicenote/pending/<note-id>.json` instead of discarding. Resume on next `/list-pending` command. | MEDIUM | Avoids "did I lose that note?" panic. Cron sweep moves stale-pending → discarded archive after N days. |

### Anti-Features (Deliberately NOT Building for Single-User v1)

Features that look like obvious wins but create scope drag, ongoing operational tax, or weaken the locked decisions.

| Feature | Why Requested | Why Problematic | Alternative |
|---------|---------------|-----------------|-------------|
| **Multi-user accounts / public bot** | "Could share with friends." | Auth, isolation, per-user state, abuse handling — multiplies surface area by 10× for zero personal benefit. Locked out of scope. | Single `user_id` allowlist. |
| **Public web UI / review dashboard** | "Telegram inline buttons are limited." | Adds an HTTP server, auth, deployment target. The 5-button approve/edit/reject covers 95% of cases. | Telegram inline keyboards + `force_reply` for edits. |
| **Hosted vector DB for dedup** | "Embedding similarity would catch more duplicates." | Locked out of scope. Adds Pinecone/pgvector/Weaviate as a dependency. v1 dedup is textual + flag-don't-skip. | Title slug + n-gram overlap. Revisit if textual misses are common AND noisy. |
| **Real-time transcription (webhook + streaming)** | "Voice-to-vault in 30 seconds." | Webhook needs a public URL or tunnel. Streaming Whisper has its own complexity. Locked out of scope. | Cron poll every 5–15 min. |
| **Telegram webhook server** | "Less latency than polling." | Public URL + TLS + ngrok/cloudflared. Operational fragility. Locked out of scope. | `getUpdates` long-poll from a cron-driven script. |
| **Mobile app (React Native / Flutter)** | "Better UX than Telegram." | Telegram IS the mobile app. Building a wrapper is pure cost. | Telegram bot. |
| **OpenAI Whisper API / Deepgram / AssemblyAI** | "Spanish accuracy might beat local Whisper." | Locked out of scope until local quality is shown insufficient. New vendor, new bill, new failure mode. | Local Whisper `medium` model; revisit after backfill. |
| **Auto-commit without review** | "Less friction." | Removes the cheap insurance against bad splits / bad translations. Locked out of scope. | Review-before-commit is the differentiator vs Voicepal. |
| **Vault PR / git-review per entry** | "Cleaner provenance." | One PR per entry × 4 entries per voice note = 200 PRs/month. Defeats capture flow. Locked out of scope. | Direct commit to `obsidian-vault` `draft` branch with frontmatter source-ID. |
| **Notion staging DB before vault** | "Symmetric with existing Notion blog flow." | Locked out of scope. Adds a hop and a sync surface. | Backfill writes straight to vault. |
| **Threaded reply chains as multi-message capture** | "I want to add to a previous note." | Doubles the state model (conversation threads). Telegram users can just send another voice note + a caption referencing the parent slug if needed (a v2 idea). | Single-shot per voice message. |
| **Text fallback (typed note instead of voice)** | "Sometimes I want to type a long thought." | Different ingestion path duplicates capture surface; user has the regular vault for typed entries. The existing video pipeline already covers short typed thoughts via `--from-transcript`. | If typed-long ever surfaces as a real need: reuse `LongNote` adapter with `source: telegram://text/<id>`. Trivial to add later. Not for v1. |
| **Embedding-based theme suggestion** | "Auto-route entries to themes." | The `kb-curator` agent already handles this. Adding a layer in voicenote duplicates the responsibility. | Reuse existing `kb-curator`. |
| **Calendar/meeting integration** | "Capture during 1:1s." | Out-of-domain. Voicenote captures voluntary reflection, not transcribed conversations. | None. |
| **Sentiment / emotion tagging** | "Track mood across entries." | Out-of-domain. Vault's value is the coaching insight, not affect tracking. | None. |
| **Multi-language vault output** | "Some quotes work better in Spanish." | Vault is 100% English; the book audience is English. Source-language quote IS preserved in frontmatter — that's the affordance. | `source_excerpt_es:` field in frontmatter; render as blockquote in entry body if pithy. |
| **Auto-discard on Telegram timeout** | "Don't let stale drafts pile up." | Silent loss of capture is worse than stale drafts. | Persist to `voicenote/pending/`; manual sweep. |
| **Auto-commit on Telegram timeout** | "Don't make me approve every one." | Defeats review-before-commit. | Persist as pending; user can `/approve-all <date>` later. |

## Feature Dependencies

```
[User-id allowlist]
    └──gates──> [Long voice message ingest]

[Long voice message ingest] ────────────┐
[Notion backfill source]    ────────────┤
                                        ├──converge──> [LongNote adapter]
                                        │                  │
                                        │                  ▼
                                        │         [Transcribe (reuse pipeline)]
                                        │                  │
                                        │                  ▼
                                        │         [LLM split (ES, auto-N)]
                                        │                  │
                                        │                  ▼
                                        │         [Per-chunk translate ES→EN]
                                        │                  │
                                        │                  ▼
                                        │         [Per-chunk extract (reuse agent)]
                                        │                  │
                                        ▼                  ▼
                              [Source frontmatter]    [Overlap flag (textual)]
                                        │                  │
                                        └──────┬───────────┘
                                               ▼
                                  [Telegram review-before-commit]
                                               │
                                  ┌──────────┬─┴────────┬───────────┐
                                  ▼          ▼          ▼           ▼
                              [Approve]   [Edit]    [Reject]   [Merge ↑]
                                  │          │          │           │
                                  ▼          ▼          ▼           ▼
                                  └──────────┴──────────┘     [Re-translate
                                              │               + re-extract]
                                              ▼                     │
                              [kb-curator vault write]<──────────────┘
                                              │
                                              ▼
                                  [Telegram success notification]


[Caption hint: /N] ──enhances──> [LLM split]
[Caption hint: title] ──enhances──> [LLM split] + [kb-curator]
[Per-chunk confidence] ──enhances──> [Telegram review-before-commit]
[Suspected-duplicate inline] ──enhances──> [Telegram review-before-commit]
[Theme conflict warning] ──enhances──> [Telegram review-before-commit]

[Backfill idempotency] ──requires──> [Notion backfill source]
[Per-subpage status table] ──requires──> [Backfill idempotency]
[Bounded parallelism] ──requires──> [Backfill idempotency]
[Dry-run mode] ──forks──> [kb-curator vault write] (replaces with dump)

[Telegram poll cursor] ──requires──> [Long voice message ingest]
[Save draft on timeout] ──requires──> [Telegram review-before-commit]
[Retry button] ──requires──> [Telegram error notification]

[Audio retention] ──independent── (write at ingest, never read except manual recovery)
```

### Dependency Notes

- **LongNote adapter is the choke point:** Telegram source and Notion source converge here. Everything downstream is source-agnostic. Maintain this discipline — any source-specific branching inside the graph is a smell.
- **Split happens on ES, translate happens per chunk:** Reversing this order flattens nuance (locked decision). Do not let prompt iteration silently re-order it.
- **Overlap flag is informational, not gating:** Bot shows it; user decides. The flag never blocks a write.
- **Audio retention is fire-and-forget:** Written at ingest, indexed by message ID, never read except for manual replay. Don't build any "audio search" feature around it.
- **kb-curator is unchanged downstream:** This is the contract. Voicenote module's job is to produce one or more (English, atomic, well-formed) thought-bundles that match the existing extractor's input shape.
- **Dry-run is a substitution at the kb-curator step:** Same graph, different terminal sink. Avoid having two parallel graphs.
- **Approve-all batches per-chunk approvals, not per-note:** Even with batch approve, each chunk's vault write still goes through kb-curator individually, because kb-curator's interrupt-on-new-theme behavior must still fire per chunk.

## MVP Definition

### Launch With (v1 = first usable capture loop)

Minimum to validate: "Spanish voice note → reviewed → English entries in vault."

- [ ] **LongNote adapter + Telegram source** — the ongoing capture surface
- [ ] **User-id allowlist** — security floor; bot rejects everyone else silently
- [ ] **Long-note pipeline graph** — transcribe → split → translate → extract → curate (one LangGraph DAG or a tight sequential equivalent)
- [ ] **LLM auto-split with caption `/N` override** — usable splitter Day 1
- [ ] **Per-chunk ES→EN translation with source-language frontmatter** — preserves nuance
- [ ] **Review-before-commit (Approve / Reject only)** — defer Edit + Merge to v1.1
- [ ] **Source + parent_note frontmatter on every entry** — traceability
- [ ] **Audio retention** — replay safety net
- [ ] **Notion backfill one-shot script** — covers the 29 Voicepal pages
- [ ] **Idempotency for backfill** — re-runnable
- [ ] **Dry-run mode for backfill** — inspect before commit
- [ ] **Textual overlap flag (title slug + first-paragraph n-gram)** — flag-don't-skip
- [ ] **Telegram poll cursor + offset persistence** — durable resume
- [ ] **Structured JSONL telemetry** — reuse `runs.jsonl` conventions
- [ ] **Telegram success/failure notifications** — single-user ops surface

### Add After Validation (v1.x — once daily capture loop is stable)

Triggered by friction observed in real use.

- [ ] **Per-chunk Edit button** — once you hit your first 90% correct chunk that wasn't worth rejecting
- [ ] **Merge adjacent chunks** — once you hit your first LLM over-segmentation
- [ ] **Per-chunk translation confidence flag** — once you log a code-switched note that translated wrong
- [ ] **Suspected-duplicate inline link** — once textual overlap fires often enough to be valuable
- [ ] **Theme-level conflict warning** — once a chapter saturates (≥20 entries) and you want signal
- [ ] **Caption title hint** — once you wish you could pre-name the thought
- [ ] **Save draft on Telegram timeout** — once you lose a draft because you didn't review in time
- [ ] **Retry button on failure** — once retry-via-typed-reply feels clunky
- [ ] **Bounded parallelism for backfill** — only if backfill v1 runs too slow (unlikely for 29 pages)
- [ ] **Per-subpage status table** — only if backfill failures get common enough to want visibility
- [ ] **Daily/weekly capture summary** — once the habit is stable and you want a "this week was N entries" report

### Future Consideration (v2+ — defer until clear demand signal)

- [ ] **Embedding-based dedup** — only when textual misses cost more than the new dependency
- [ ] **Text-message fallback (typed long note)** — only if you find yourself typing long notes into Telegram bot DMs
- [ ] **Multi-message thread capture (draft mode)** — only if you start hitting Telegram's 20 min voice limit and need stitching
- [ ] **Source quote in entry body** (render `source_excerpt_es` as blockquote in published Markdown) — once you want bilingual entries
- [ ] **Auto-commit policy override for trusted theme** — e.g. `[[deliberate-discomfort]]` is so well-trained that splits land well enough to skip review; defer until real evidence
- [ ] **Webhook ingestion** — only if cron-poll latency becomes painful

## Feature Prioritization Matrix

| Feature | User Value | Implementation Cost | Priority |
|---------|------------|---------------------|----------|
| Long voice message ingest | HIGH | MEDIUM | P1 |
| User-id allowlist | HIGH | LOW | P1 |
| LLM auto-split (ES) | HIGH | MEDIUM | P1 |
| Per-chunk translate ES→EN | HIGH | LOW | P1 |
| Review-before-commit (Approve/Reject) | HIGH | MEDIUM | P1 |
| Source frontmatter | HIGH | LOW | P1 |
| Audio retention | MEDIUM | LOW | P1 |
| Notion backfill one-shot | HIGH | MEDIUM | P1 |
| Backfill idempotency | HIGH | LOW | P1 |
| Backfill dry-run | HIGH | LOW | P1 |
| Telegram poll cursor | HIGH | LOW | P1 |
| Textual overlap flag | MEDIUM | LOW | P1 |
| Structured telemetry | MEDIUM | LOW | P1 |
| Telegram notifications | HIGH | LOW | P1 |
| Caption hint (/N, title) | MEDIUM | LOW | P2 |
| Per-chunk Edit button | MEDIUM | MEDIUM | P2 |
| Merge adjacent chunks | MEDIUM | MEDIUM | P2 |
| Per-chunk confidence flag | MEDIUM | LOW | P2 |
| Suspected-duplicate inline | MEDIUM | LOW | P2 |
| Theme conflict warning | LOW | LOW | P2 |
| Save draft on timeout | MEDIUM | MEDIUM | P2 |
| Retry button | MEDIUM | LOW | P2 |
| Per-subpage status table | LOW | LOW | P2 |
| Bounded parallelism backfill | LOW | LOW | P2 |
| Daily capture summary | LOW | LOW | P3 |
| Title hint in preview | MEDIUM | LOW | P2 |
| Embedding dedup | LOW | HIGH | P3 |
| Text fallback | LOW | LOW | P3 |
| Thread chain capture | LOW | HIGH | P3 |

**Priority key:**
- P1: Required for v1 launch (loops in `## MVP / Launch With`)
- P2: v1.x add-on (locked-in mental model, ship as friction surfaces)
- P3: v2+ candidate (defer until demand signal)

## Competitor Feature Analysis

Reference systems for feature inspiration (not stack choices).

| Feature | Voicepal | Audionotes | Rambler (research) | Speakwise | Our Approach |
|---------|----------|------------|--------------------|-----------|--------------|
| Long voice ingest | App-native, multi-platform | App-native, file upload, YouTube | Desktop research prototype | iPhone app, Notion sync | Telegram bot (single-user, no app) |
| Splitting | None (one stream per recording) | Auto-summarize, doesn't atomize | Manual + Semantic Split | None mentioned | LLM auto-split into atomic thoughts |
| Translation | None (single language) | Multi-language transcript | English only | English-first | ES→EN per chunk; preserve ES quote |
| Review | "Polished transcript" alongside raw, manual export | Edit-then-export | Manual edit + merge | "AI summaries" | Inline keyboard per chunk; batch Approve-all |
| Dedup | None | None | N/A | None | Textual overlap → flag-don't-skip |
| Storage | App-cloud | App-cloud | Research artifact | Notion sync | Local vault (Obsidian) + Notion derivatives |
| Multi-export presets | Yes (newsletter, X, blog, LinkedIn) | Yes | No | Notion-first | Reuses existing pipeline (vault → Notion → WP → YT) |
| Original audio kept | Yes (in-app) | Yes (in-app) | Yes | Unclear | Yes, `voicenote/audio/` local + gitignored |
| Follow-up questions | Yes ("shadow reader") | Some | N/A | Unclear | Out of scope (deliberately) |
| Single-user only | No (SaaS) | No (SaaS) | N/A | No (SaaS) | Yes (allowlist) |
| Self-hosted | No | No | Research only | No | Yes (local Whisper, local LLM-via-subscription, local vault) |

**Takeaways from competitor analysis:**
- All SaaS competitors keep raw audio next to the transcript — table stakes signal validated.
- None of them atomize long notes into multiple distinct outputs; this is a genuine differentiator and IS the core value (multiple atomic English thoughts per Spanish voice note).
- Rambler's research finding that "manual split + LLM semantic split" beats pure auto-split argues for the caption-hint feature (manual override available, auto by default).
- None offer dedup against an external vault. The flag-don't-skip approach is bespoke to the dual-source overlap reality (Notion backfill + future video pipeline can describe the same thought).

## Open Questions for Phase-Specific Research

These surfaced during this research but are too implementation-detail to settle at the feature-landscape phase. Flag for downstream phases.

1. **Whisper Spanish quality benchmark.** Local `medium` model on a 5–10 min cleaned Spanish voice note — is the transcript good enough that splitter doesn't have to fight artifacts? (Validate during the first backfill run; have OpenAI Whisper API as a contingency.)
2. **LLM split prompt design.** Does the splitter need to see the whole transcript at once, or work in a sliding window? Probably whole-at-once for 5–10 min notes (≤ 2000 tokens), but ~20 min notes might push 5000+ tokens. Test on the 29-page corpus.
3. **Translation prompt: per-chunk vs single-shot full transcript translated chunk-by-chunk.** Per-chunk preserves nuance but loses context. Single-shot with chunk markers preserves context but is more parse-fragile. (Decision belongs in `extract`/`translate` node design phase.)
4. **Inline keyboard layout density.** Telegram inline keyboards top out around 3–4 buttons per row before tap-targets get small. With Approve/Edit/Reject/Merge + a "Show duplicate" link, layout needs to be thought through.
5. **`obsidian-vault` submodule write discipline.** Existing pipeline already writes to the submodule; voicenote must inherit the same git hygiene (commit through submodule explicitly, don't leave it dirty). Surface this in PITFALLS.
6. **Backfill ordering.** 29 subpages — process by Notion `created_time` (chronological) or by inferred topic clustering (e.g. all `[[deliberate-discomfort]]` first to let kb-curator see them as a batch)? Probably chronological by default; flag for backfill phase.

## Sources

- [Voicepal review — actionable notes](https://actionablenotes.substack.com/p/productivity-tool-review-voicepal) — original-audio-alongside-transcript pattern, Streams as topic collections, polished transcript review
- [Voicepal review — navid.me](https://navid.me/voicepal-review/) — app workflow, follow-up questions, multi-format export presets
- [Voicepal review — hardlyhamilton](https://hardlyhamilton.com/2024/12/20/voicepal-review-ali-abdaal-blog-writing/) — workflow context for the 29 Voicepal subpages being migrated off
- [Audionotes — PKM voice tools](https://www.audionotes.app/blog/best-personal-knowledge-management-tools) — competitive landscape
- [Speakwise — Obsidian alternatives 2026](https://speakwiseapp.com/blog/obsidian-alternatives) — competing voice-to-vault tool, Notion-sync first
- [Rambler: Supporting Writing With Speech via LLM-Assisted Gist Manipulation — CHI 2024](https://dl.acm.org/doi/10.1145/3613904.3642217) — manual + semantic split/merge UX research, validates the per-chunk edit affordance
- [Towards Multi-Level Transcript Segmentation — Interspeech 2025](https://www.isca-archive.org/interspeech_2025/freisinger25_interspeech.pdf) — acoustic-cue-aware LLM splitting; informs whether pause durations should be included in the transcript fed to the splitter
- [Long-Form Speech Translation through Segmentation — EMNLP 2023](https://aclanthology.org/2023.findings-emnlp.19.pdf) — supports the locked decision to segment in source language before translating
- [Telegram Bot API](https://core.telegram.org/bots/api) — `sendVoice`, `getUpdates` offset semantics, inline keyboard callback_query, `force_reply`
- [Telegram Bot Buttons API](https://core.telegram.org/api/bots/buttons) — inline keyboard payload limits, callback data 1–64 bytes
- [Telegram Bot Features](https://core.telegram.org/bots/features) — voice message handling, polling vs webhook tradeoffs
- [Inline keyboards lesson — TelegramBots](https://rubenlagus.github.io/TelegramBotsDocumentation/lesson-6.html) — callback_query pattern, editing message text after callback
- [Backfilling with idempotency keys — n8n blog](https://medium.com/@kaushalsinh73/n8n-backfills-with-idempotency-keys-recover-pipelines-without-duplicates-d0be7aaddaa7) — pattern for resumable bulk ingest without duplicate writes
- [Idempotent data pipelines — Start Data Engineering](https://www.startdataengineering.com/post/why-how-idempotent-data-pipeline/) — partition overwrite / MERGE patterns adapted to "processed page IDs"
- [Backfilling historical data — ml4devs](https://www.ml4devs.com/what-is/backfilling-data/) — dry-run + idempotent backfill discipline
- [Fuzzy near-duplicate text detection — Springer](https://link.springer.com/chapter/10.1007/978-3-031-67348-1_19) — shingling + n-grams as cheap first pass for textual overlap
- [Layman's guide to fuzzy document dedup — Towards Data Science](https://towardsdatascience.com/a-laymans-guide-to-fuzzy-document-deduplication-a3b3cf9a05a7) — tiered approach (cheap filter → expensive metric) — informs flag-don't-skip + future-considered embedding upgrade
- [Best second brain apps 2026 — Atlasworkspace](https://www.atlasworkspace.ai/blog/best-second-brain-apps) — Mem.ai approach (no folders, AI-organized), context for not building auto-organization (kb-curator handles it)

---
*Feature research for: long-form Spanish voice → English atomic-thought vault, single-user, Telegram + Notion-backfill sources*
*Researched: 2026-05-18*
