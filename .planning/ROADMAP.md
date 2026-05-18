# Roadmap: Voicenote

## Overview

Voicenote turns long-form Spanish voice notes (Telegram, plus a one-shot Notion backfill) into atomic English coaching-thought entries in `obsidian-vault/gonzalo-book/`. The build order is **PoC-first** — Phase 0 validates the two HIGH-risk assumptions (Spanish Whisper quality + LLM split reliability) on real fixtures before any module scaffolding. Phase 1 delivers an end-to-end Telegram intake that produces extraction reports on disk (no review yet) — surfacing 5 HIGH-severity Whisper/privacy pitfalls early. Phase 2 ships the Notion backfill so the 29 Voicepal subpages become the natural test corpus for the splitter prompt AND validate the dual-source `LongNote` abstraction *before* the review UX layers async-pause complexity on top. Phase 3 adds the inline-keyboard review state machine; Phase 4 lands the one cross-boundary refactor into `pipeline/vault_writer.py` and closes the existing `[high]` CONCERNS.md vault-submodule-dirty bug. Phase 5 hardens to daily-brief operational parity (systemd timer + cron watchdog + cost gate + theme cap + dead-letter replay). Each phase adds its own unit tests as part of completion; smoke E2E ships in Phase 5.

## Phases

**Phase Numbering:**
- Integer phases (0, 1, 2…): Planned milestone work
- Decimal phases (2.1, 2.2): Urgent insertions (marked with INSERTED)

- [ ] **Phase 0: PoC & Pre-Flight** - Validate Spanish Whisper + LLM split + chunk-extraction quality; lock cost forecast; close pre-rollout retry-bound gap
- [ ] **Phase 1: Telegram Capture + Processing Pipeline** - End-to-end intake (Telegram → transcribe → split → translate → extract) producing extraction reports on disk; no review yet
- [ ] **Phase 2: Notion Backfill (Dual-Source Validation)** - 29 Voicepal subpages flow through identical pipeline via `LongNote` adapter; dry-run is canonical integration test
- [ ] **Phase 3: Review UX (Inline-Keyboard HITL)** - Per-chunk Approve/Reject via Telegram callbacks; drafts survive restart and 24-h delays
- [ ] **Phase 4: Vault Hand-Off & Submodule Commit** - Shared `apply_curation_plan` + `commit_vault_submodule` close existing `[high]` CONCERNS.md bug; approved drafts land in vault with full provenance
- [ ] **Phase 5: Operations & Hardening** - systemd timer, watchdog, cost-forecast gate, dead-letter replay, smoke E2E, PII redaction; matches daily-brief production posture

## Phase Details

### Phase 0: PoC & Pre-Flight
**Goal**: De-risk the two HIGH-risk technical assumptions (Spanish Whisper quality + LLM split reliability) and the two HIGH-risk operational gates (Voicepal kill-list + LLM quota forecast) BEFORE any module scaffolding lands.
**Depends on**: Nothing (first phase)
**Requirements**: POC-01, POC-02, POC-03, POC-04, POC-05
**Success Criteria** (what must be TRUE):
  1. On ≥3 real ~10-min ES voice notes, `large-v3` (or `large-v3-turbo`) transcripts are readable enough that a human can identify coaching-thought boundaries with the same confidence as the original audio — chosen Whisper model + WER signal documented in `.planning/research/`.
  2. The draft `voicenote-splitter` prompt segments those same transcripts into atomic chunks with ≥80% boundary agreement vs Gonzalo's hand-judged segmentation on ≥3 fixtures.
  3. Feeding a translated chunk to the existing `coaching-thought-extractor` produces output qualitatively indistinguishable from an entry Gonzalo would have written manually from the same source — fork-vs-reuse decision for the extractor prompt is recorded.
  4. `python -m pipeline.cost_forecast --voicenote` prints projected per-note tokens, USD-equivalent, and Anthropic-Ultra-quota share for both the 29-subpage backfill and typical weekly Telegram capture cadence — no replay or backfill runs until this exists.
  5. Voicepal kill-list is in `OPERATIONS.md` (webhooks / scheduled syncs / Notion automations enumerated) with a documented 7-day no-op observation window before subscription cancel.
**Plans**: TBD

### Phase 1: Telegram Capture + Processing Pipeline
**Goal**: An end-to-end Telegram intake that turns Spanish voice messages into reviewable extraction reports on disk — restart-safe, idempotent on `update_id`, allowlist-gated, with the full transcribe → split → translate → extract pipeline running source-agnostically — but with NO review UX yet (drafts dropped to disk + Telegram notification).
**Depends on**: Phase 0
**Requirements**: CAP-01, CAP-02, CAP-03, CAP-04, TG-01, TG-02, TG-03, TG-04, TG-05, TG-06, PROC-01, PROC-02, PROC-03, PROC-04, PROC-05, PROC-06, PROC-07, TEST-01, TEST-02
**Success Criteria** (what must be TRUE):
  1. Sending a 5-min Spanish voice message to the new bot ends with N extraction reports written to `voicenote/state/` (one per atomic chunk), the raw `.ogg` retained under `voicenote/audio/`, and a Telegram reply confirming "N draft entries ready" — all within one cron tick after the message arrives.
  2. Restarting the worker mid-processing, or polling twice in quick succession, never re-processes a Telegram update: `telegram_cursor.last_update_id` and `INSERT OR IGNORE` on `(source_kind, source_ref)` guarantee idempotency end-to-end.
  3. A message from any `user_id` other than `VOICENOTE_ALLOWED_USER_ID` is silently dropped (no Telegram reply, no row inserted, no `.ogg` downloaded) — closes the `_wait_reply` chat-id-gap from `CONCERNS.md`.
  4. A voice message with `file_size` > 20 MB or a Whisper failure produces a user-facing Telegram error with a `run_id` + stage name (NO transcript content), the failure routes to `voicenote/state/dead_letter/`, and the `.ogg` is retained for replay.
  5. A caption of `/3` on the voice message overrides splitter auto-detect to force exactly 3 chunks; per-chunk translation surfaces a `HIGH/MEDIUM/LOW` confidence flag on the chunk payload.
  6. `python -m unittest discover voicenote/tests` exercises source adapters (allowlist gate, cursor persistence, idempotent insert), processing stages (splitter contract, translator confidence flag, extractor wrapper), and the SQLite DAL — `httpx.MockTransport` swaps in for Telegram + Anthropic — and every test passes without touching real network.
**Plans**: TBD
**UI hint**: yes

### Phase 2: Notion Backfill (Dual-Source Validation)
**Goal**: The 29 Voicepal subpages flow through the identical processing pipeline as Telegram via the `LongNote` adapter contract — proving the source-agnostic abstraction holds across audio (Telegram) and text-only (Notion) inputs — with idempotency, pacing, and a dry-run that doubles as the canonical integration test for the splitter on a real ES corpus.
**Depends on**: Phase 1
**Requirements**: NTN-01, NTN-02, NTN-03, NTN-04, NTN-05, TEST-04
**Success Criteria** (what must be TRUE):
  1. `python -m voicenote backfill --source notion --limit 5 --dry-run` reads 5 Voicepal subpages, runs each through split → translate → extract, and renders proposed entries as markdown under `voicenote/dryrun/<run-id>/` without writing to the vault.
  2. Re-running the same backfill command (full or `--limit N`) processes only pages NOT already in `processed_notion_pages` — page IDs that completed previously are skipped silently; safe to re-run after a crash.
  3. The Notion fetch never exceeds the existing `_PACE_SECONDS = 0.4` pacer (~2.5 req/s under the 3 req/s Notion cap), handles `has_more`/`next_cursor` pagination, and recurses child blocks up to depth 8.
  4. The full 29-subpage `--dry-run` produces a per-subpage status rollup (queued / processed / skipped-duplicate / failed) — and is the canonical integration test signed off before any `--apply` run.
  5. `processing/` code is unchanged between Phase 1 and Phase 2: the Notion path skips `transcribe.py` (because `audio_path is None`) but split / translate / extract / present-drafts execute byte-identical to the Telegram path.
**Plans**: TBD

### Phase 3: Review UX (Inline-Keyboard HITL)
**Goal**: A per-chunk Approve/Reject review flow over Telegram inline keyboards that survives process restart, multi-hour user delays, and double-clicks — with `answer_callback_query` < 1 s and 24-h expiry that archives stale drafts without auto-committing or silently discarding.
**Depends on**: Phase 2
**Requirements**: REV-01, REV-02, REV-03, REV-04, REV-05, REV-06, REV-07
**Success Criteria** (what must be TRUE):
  1. A 4-chunk long-note produces 4 separate Telegram preview messages, each showing bold title + EN body + ES source excerpt + confidence flag (⚠️ on LOW) + possible-duplicate link (if flagged) + [Approve] / [Reject] buttons.
  2. Tapping a button acks via `answer_callback_query` in under 1 s (well inside the 15-s Telegram timeout) BEFORE any heavy work; the heavy commit work runs on the next cron tick reading the updated `drafts.decision` from SQLite.
  3. Approving 3 of 4 chunks and rejecting 1 commits exactly 3 vault entries (via `kb-curator`, one at a time) and leaves the rejected draft logged with timestamp + button label in `drafts` — no partial-state corruption, no double-write.
  4. Restarting the worker process between the user receiving the preview and tapping a button preserves all pending drafts; the next worker tick resumes correctly with no re-prompt and no double-processing.
  5. Drafts pending > 24 h are moved to `voicenote/pending/expired/` by a cron sweep — never auto-committed, never silently deleted; user is nudged via Telegram before expiry.
  6. Unit + integration tests using `httpx.MockTransport` for Telegram cover: answer-callback-first ordering, double-click no-op via `decision='pending'` guard, 24-h expiry sweep, partial-approval commit accounting.
**Plans**: TBD
**UI hint**: yes

### Phase 4: Vault Hand-Off & Submodule Commit
**Goal**: One cross-boundary refactor lifts `kb_curator._apply_proceed` into `pipeline/vault_writer.py:apply_curation_plan` as a shared helper, adds `commit_vault_submodule()` to close the existing `[high]` CONCERNS.md dirty-submodule bug, and wires the voicenote vault-write path with full provenance (`source:`, `parent_note:`, `source_excerpt_es:`, `## Source (ES)` block) and overlap flagging.
**Depends on**: Phase 3
**Requirements**: VAULT-01, VAULT-02, VAULT-03, VAULT-04, VAULT-05, VAULT-06
**Success Criteria** (what must be TRUE):
  1. After a long-note's drafts are all approved, the corresponding vault entries exist on disk under `obsidian-vault/gonzalo-book/entries/`, are committed inside the submodule (`git status` clean inside `obsidian-vault/`), and the parent repo's submodule pointer is updated — closes CONCERNS.md `[high]` vault-dirty scar.
  2. Every voicenote-created entry's frontmatter carries `source: telegram://msg/<id>` (or `notion://page/<id>`), `parent_note: <id>` linking sibling chunks split from the same long note, `source_excerpt_es: |` with the verbatim original Spanish, and a `captured_at` timestamp.
  3. Every voicenote entry's body ends with a `## Source (ES)` section rendering the verbatim ES quote as a blockquote — vault stays English-uniform for retrieval, ES voice recoverable on demand.
  4. An entry whose title-slug or first paragraph has Jaccard 3-gram similarity ≥ threshold against an existing vault entry is written anyway and tagged `[[possible-duplicate-of:<slug>]]` — flag-don't-skip policy enforced in test fixture.
  5. The existing video pipeline still writes entries correctly via the same shared `apply_curation_plan` helper (verified by `tests/smoke_pipeline.sh` green); a concurrent voicenote vault-write does not race because of `flock` on `voicenote/state/vault.lock`.
  6. Unit tests cover: frontmatter generation across both source kinds, ES blockquote rendering, Jaccard overlap math, the `flock` mutual-exclusion contract.
**Plans**: TBD

### Phase 5: Operations & Hardening
**Goal**: Match daily-brief operational parity — short-lived systemd timer with bounded retries, heal-then-notify watchdog, bounded prompt growth, dead-letter replay, smoke E2E, PII-redacted logs, and a pre-run cost-forecast gate that refuses to burn quota on a forecast violation.
**Depends on**: Phase 4
**Requirements**: OPS-01, OPS-02, OPS-03, OPS-04, OPS-05, OPS-06, OPS-07, OPS-08, TEST-03
**Success Criteria** (what must be TRUE):
  1. `systemctl --user enable --now painforwisdom-voicenote-poll.timer` runs the worker every 10 min as a `Type=oneshot` drain (no persistent daemon); `Restart=on-failure`, `StartLimitBurst=3` in 2 h matches the daily-brief shape exactly.
  2. `scripts/check_voicenote_freshness.sh` detects a stuck pipeline (any row in `*_ING` state for > 1 h OR timer not active), runs `systemctl --user reset-failed && start` (heal), and only THEN posts a redacted alert to the existing daily-summary Telegram channel (heal-then-notify per commit `2e21bd9`).
  3. Every voicenote stage appends a row to `runs.jsonl` with stage = `voicenote.transcribe` / `voicenote.split` / `voicenote.translate` / `voicenote.extract` / `voicenote.review` / `voicenote.commit` — matches existing telemetry schema; redaction tested.
  4. A failure in any stage routes the note to `voicenote/state/dead_letter/` with full context; `python -m voicenote replay --id <note_id>` resumes from the FAILED state and reaches `COMMITTED` (or fails loudly again — never silently).
  5. `tests/smoke_voicenote.sh` drives the full pipeline against ≥2 ES fixture transcripts (no real audio) and exits 0 in CI; matches `tests/smoke_pipeline.sh` pattern.
  6. Telegram error replies and `runs.jsonl` entries NEVER contain raw transcript prose (verified by a redaction unit test); `.ogg` files stay on disk only; `pipeline/retry.py:_resume_graph` uses bounded `MAX_REMINDERS` (closes `[med]` CONCERNS.md gap).
  7. Before any backfill or large replay, `python -m pipeline.cost_forecast --voicenote` runs as a pre-flight gate; the worker refuses to proceed if projected quota share > 80%.
**Plans**: TBD

## Progress

**Execution Order:**
Phases execute in numeric order: 0 → 1 → 2 → 3 → 4 → 5

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 0. PoC & Pre-Flight | 0/TBD | Not started | - |
| 1. Telegram Capture + Processing Pipeline | 0/TBD | Not started | - |
| 2. Notion Backfill | 0/TBD | Not started | - |
| 3. Review UX | 0/TBD | Not started | - |
| 4. Vault Hand-Off & Submodule Commit | 0/TBD | Not started | - |
| 5. Operations & Hardening | 0/TBD | Not started | - |

---
*Roadmap created: 2026-05-18*
*Granularity: standard (6 phases)*
*Coverage: 52/52 v1 requirements mapped*
