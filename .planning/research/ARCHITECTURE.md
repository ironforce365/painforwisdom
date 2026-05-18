# Architecture Research — `voicenote/` Module

**Domain:** Long-form voice-note → personal-vault pipeline (Telegram bot + Notion one-shot backfill, shared processing pipeline, single-user).
**Researched:** 2026-05-18
**Confidence:** HIGH for module layout / state-machine / hand-off (grounded in existing `pipeline/` patterns + `python-telegram-bot` v22 docs via Context7); MEDIUM for concurrency caps (Notion 3 req/s and Whisper local capacity are well-known, but LLM rate share against the Anthropic Ultra subscription is empirical).

## TL;DR — The Five Decisions That Matter

1. **Plain Python pipeline, not LangGraph.** The voicenote flow is short, linear (transcribe → split → translate → extract → review → commit), the only HITL pause is the Telegram review which is naturally idempotent via persisted draft state — there is no DAG fan-out and no need for `interrupt()` resume. Reuse `pipeline.llm.call_llm`, `pipeline.runtime.load_agent_prompt`, `pipeline.notion_client`, `pipeline.telegram.send`. Skip LangGraph + SqliteSaver — they are over-spec for a 5-step linear flow with one external pause.
2. **SQLite for persistent state, not JSONL or Telegram-message-as-state.** One DB file `voicenote/state/voicenote.db` with three tables (`long_notes`, `drafts`, `telegram_cursor`). JSONL would force a full-file rewrite on every status update; encoding state into Telegram message text loses idempotency on re-poll. SQLite gives transactional status transitions and lets cron-spawned workers and on-demand CLI runs share the same store safely.
3. **Cron-spawned one-shot worker, NOT a long-lived bot process.** A `python -m voicenote.worker --poll-once` invocation called by a systemd timer (matching the existing `painforwisdom-daily-brief.timer` pattern) calls `getUpdates` with `offset = persisted_cursor + 1`, processes whatever arrived, persists the new cursor, exits. No idle process, no orphaned getUpdates connection during deploys, watchdog story identical to daily-brief.
4. **Source adapters are iterators producing `LongNote` records, not strategy classes.** A tiny abstract protocol with one method `iter_pending() -> Iterator[LongNote]` keeps the surface flat and Pythonic. `sources/telegram.py` yields one per new voice message; `sources/notion.py` yields one per unprocessed Voicepal subpage. The processing pipeline does not know which produced the note.
5. **kb-curator hand-off is a direct in-process Python call**, NOT a queue or file-trigger. The voicenote module imports and reuses `coaching-thought-extractor` + `kb-curator` agents via the existing `pipeline.llm.call_llm` + `pipeline.runtime.load_agent_prompt` machinery. The vault writer is `pipeline.nodes.kb_curator._apply_proceed`-equivalent logic, lifted into a reusable helper (or refactored in-place — see Build Order). No new queue file the existing pipeline has to poll.

---

## Module Layout

```
voicenote/
├── __init__.py                   # Package init; silences nothing — pipeline/__init__.py already handled LC warning at process scope
├── __main__.py                   # `python -m voicenote` → dispatches subcommands (poll-once, backfill, status, replay)
├── cli.py                        # argparse + subcommand dispatch; profile selection mirrors pipeline/run.py
├── worker.py                     # One-shot polling worker (called by systemd timer); orchestrates source → processing → review
├── state/                        # Runtime state (gitignored)
│   ├── voicenote.db              # SQLite: long_notes, drafts, telegram_cursor, processed_pages
│   └── audio/                    # Retained .ogg per note (gitignored; replayable on failure)
├── sources/
│   ├── __init__.py               # Defines Source protocol + LongNote dataclass
│   ├── telegram.py               # TelegramSource — getUpdates → LongNote (voice msg → downloaded .ogg)
│   └── notion.py                 # NotionSource — list Voicepal subpages → LongNote (page body → faux transcript)
├── processing/
│   ├── __init__.py
│   ├── transcribe.py             # Shells extract_transcription.sh (reuse), Spanish-mode flag, CUDA→CPU fallback
│   ├── split.py                  # LLM call: ES transcript → list of atomic ES chunks (new agent prompt)
│   ├── translate.py              # LLM call: ES chunk → EN chunk (new agent prompt OR inline system message)
│   └── extract.py                # Reuses coaching-thought-extractor.md per EN chunk (same as pipeline/nodes/extract.py)
├── review/
│   ├── __init__.py
│   ├── presenter.py              # Formats draft entries → Telegram message + inline button payload
│   └── handler.py                # Consumes callback_query updates → flips draft status (approved/rejected/edit)
├── vault/
│   ├── __init__.py
│   └── writer.py                 # Wraps the kb-curator hand-off — calls coaching-thought-extractor result → kb-curator agent → submodule write + commit
├── repo.py                       # SQLite DAL: get/put LongNote, get/put Draft, cursor advance, idempotency lookups
├── models.py                     # @dataclass LongNote, Draft, NoteStatus enum
├── bot_helpers.py                # Thin wrappers over pipeline.telegram.send + python-telegram-bot's Bot for file download & inline keyboards
├── allowlist.py                  # `assert_authorized(user_id)` — env-driven user_id allowlist (single ID for v1)
└── tests/                        # stdlib unittest, mirrors pipeline conventions
    ├── test_repo.py
    ├── test_split.py
    ├── test_review_state_machine.py
    ├── test_telegram_source_offset.py
    └── fixtures/
        ├── voicepal_page_sample.json
        └── transcript_es_long.txt
```

### Why this layout (one-line each)

- **Top-level `cli.py` + `worker.py` separation** matches `pipeline/run.py` (CLI / orchestrator) + `pipeline/graph.py` (driver) split. `cli.py` parses argv + dispatches; `worker.py` owns the actual run loop.
- **`sources/` is the substitution boundary** — the same `Iterator[LongNote]` protocol means backfill and ongoing capture share `processing/` + `review/` + `vault/` without conditional code paths.
- **`processing/` is plain-function modules**, one stage per file, mirroring `pipeline/nodes/`. Each function `(state: LongNote, db: Repo) -> LongNote` updates status + persists. No LangGraph.
- **`review/` carries the bot UI surface** — the only place that knows about inline keyboards, callback_query handling, and Telegram message formatting. Everything else is source-agnostic.
- **`vault/writer.py` is the single sink** — both the Telegram path and the Notion-backfill path go through here. Reuses the existing kb-curator agent prompt + LLM call + submodule write logic.
- **`repo.py` is the only module that opens the SQLite connection** — keeps the persistence concern in one file; tests can substitute an in-memory `:memory:` DB.
- **`state/` under the module, not at repo root** — matches `pipeline/state/` (themes.db, theme_stats.json). Gitignored. Audio retained alongside.

---

## The `LongNote` Abstraction

### Fields (dataclass — no Pydantic, matches conventions)

```python
# voicenote/models.py
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import List, Optional

class NoteStatus(str, Enum):
    PENDING       = "pending"        # row inserted by source adapter; nothing else done
    TRANSCRIBING  = "transcribing"   # Whisper in progress
    TRANSCRIBED   = "transcribed"    # ES transcript on disk
    SPLITTING     = "splitting"
    SPLIT         = "split"          # chunks_es persisted as JSON list
    TRANSLATING   = "translating"
    TRANSLATED    = "translated"     # chunks_en persisted
    EXTRACTING    = "extracting"
    EXTRACTED     = "extracted"      # one extraction_report per chunk persisted
    AWAITING_REVIEW = "awaiting_review"  # Telegram review message sent; awaiting button press
    APPROVED      = "approved"       # user pressed Approve on all drafts
    REJECTED      = "rejected"       # user pressed Reject — note discarded
    COMMITTING    = "committing"     # kb-curator running; vault write + submodule commit
    COMMITTED     = "committed"      # done; vault entries written + pushed
    FAILED        = "failed"         # terminal — see error column; eligible for replay

@dataclass
class LongNote:
    id: str                          # source-stable id: telegram://msg/<chat_id>/<message_id> or notion://page/<page_id>
    source_kind: str                 # "telegram" | "notion"
    source_ref: str                  # native id (message_id, page_id) for re-poll idempotency
    received_at: datetime
    status: NoteStatus
    audio_path: Optional[Path] = None        # .ogg for telegram; None for notion (text-only)
    transcript_es_path: Optional[Path] = None
    chunks_es: List[str] = field(default_factory=list)
    chunks_en: List[str] = field(default_factory=list)
    extraction_reports: List[str] = field(default_factory=list)  # one extraction_report per chunk (raw LLM text)
    draft_ids: List[str] = field(default_factory=list)            # FK → drafts table
    parent_note_id: str = ""         # = self.id; all drafts/entries share this for sibling linking
    error: Optional[str] = None      # populated when status == FAILED
    updated_at: datetime = field(default_factory=datetime.utcnow)
```

### Persistence layer — SQLite, one DB file

`voicenote/state/voicenote.db` schema (kept narrow on purpose — same minimalism as `pipeline/state/themes.db`):

```sql
CREATE TABLE long_notes (
    id              TEXT PRIMARY KEY,
    source_kind     TEXT NOT NULL,
    source_ref      TEXT NOT NULL,
    received_at     TEXT NOT NULL,
    status          TEXT NOT NULL,
    audio_path      TEXT,
    transcript_es_path TEXT,
    chunks_es_json  TEXT,          -- JSON list[str]
    chunks_en_json  TEXT,
    extraction_reports_json TEXT,  -- JSON list[str] (one per chunk)
    parent_note_id  TEXT NOT NULL,
    error           TEXT,
    updated_at      TEXT NOT NULL,
    UNIQUE(source_kind, source_ref)  -- idempotency key
);

CREATE TABLE drafts (
    id              TEXT PRIMARY KEY,    -- uuid4
    long_note_id    TEXT NOT NULL REFERENCES long_notes(id),
    chunk_index     INTEGER NOT NULL,    -- 0..N-1 within parent
    title           TEXT NOT NULL,        -- proposed entry title (slugifiable)
    proposed_slug   TEXT NOT NULL,
    entry_markdown  TEXT NOT NULL,        -- full proposed entry body (vault-shaped)
    proposed_themes_json    TEXT NOT NULL,
    proposed_frameworks_json TEXT NOT NULL,
    overlap_flag    TEXT,                 -- e.g. "possible-duplicate-of:2026-04-13-passion-as-high-performance"
    decision        TEXT NOT NULL,        -- "pending" | "approved" | "rejected" | "edit_requested"
    telegram_message_id INTEGER,          -- the review prompt message id (for editMessageReplyMarkup on decision)
    decided_at      TEXT,
    UNIQUE(long_note_id, chunk_index)
);

CREATE TABLE telegram_cursor (
    bot_id          INTEGER PRIMARY KEY,  -- in case >1 bot ever; v1 has one row
    last_update_id  INTEGER NOT NULL
);

CREATE TABLE processed_notion_pages (
    page_id         TEXT PRIMARY KEY,
    long_note_id    TEXT NOT NULL REFERENCES long_notes(id),
    processed_at    TEXT NOT NULL
);
```

**Why SQLite over JSONL:**
- Status transitions need to be transactional — a worker crash mid-`status='extracting'` should leave the row exactly in `extracting`, recoverable by replay. JSONL append-on-update means full rewrite.
- Multiple processes will touch the same store: cron worker polls, on-demand `python -m voicenote.replay --id <X>` runs. SQLite's file-lock semantics handle this; JSONL does not without ad-hoc locking.
- Idempotency keys (`UNIQUE(source_kind, source_ref)`, `processed_notion_pages.page_id`) get index support for free.
- The existing pipeline already uses SQLite for both LangGraph checkpoints AND `themes.db` — zero new dependency.

**Why not Telegram-message-as-state:**
- Loses idempotency on re-poll (a message edit is not a status); breaks if the user deletes a message; round-tripping state through a third party is the kind of fragility `CONCERNS.md` calls out.

---

## Source Adapter Pattern

### Protocol (one method, ~10 LOC)

```python
# voicenote/sources/__init__.py
from __future__ import annotations
from typing import Iterator, Protocol
from voicenote.models import LongNote

class Source(Protocol):
    name: str
    def iter_pending(self) -> Iterator[LongNote]: ...
```

A `Protocol`, not an ABC: matches the no-Pydantic / no-ABCs idiom of the pipeline. Duck-typed; tests can pass any object with the same signature.

### `sources/telegram.py` — incremental, idempotent

- Reads `telegram_cursor.last_update_id` from `voicenote.db`.
- Calls `Bot.get_updates(offset=last + 1, timeout=0, allowed_updates=["message", "callback_query"])` — one shot, no long-poll, returns immediately.
- For each `message.voice` from an allow-listed `user_id`:
  - Downloads the `.ogg` to `voicenote/state/audio/<YYYY-MM-DD>__<message_id>.ogg` via `Bot.get_file().download_to_drive(...)`.
  - Builds `LongNote(id=f"telegram://msg/{chat_id}/{message_id}", source_kind="telegram", source_ref=str(message_id), ...)`.
  - Inserts with `INSERT OR IGNORE` (the unique constraint silently no-ops on retry).
  - Yields the LongNote.
- After iteration completes (NOT during), advances `telegram_cursor.last_update_id` in a transaction. This means a crash mid-batch re-processes the batch — safe because the insert is idempotent.
- `callback_query` updates are routed to `review/handler.py` (advances note status; does NOT yield a new LongNote).
- Rejects non-allow-listed senders silently (don't reply; closes the chat-id-spoof gap noted in `CONCERNS.md`).

### `sources/notion.py` — one-shot backfill

- Lists 29 subpages under the Voicepal parent page via `notion_client.blocks.children.list` + `pages.retrieve` (use existing `pipeline.notion_client.get_client()`).
- For each page NOT already in `processed_notion_pages`:
  - Extracts the page body as a single ES string (faux "transcript" — skips the transcribe stage; processing pipeline gates on `audio_path is None` to no-op transcription).
  - Builds `LongNote(id=f"notion://page/{page_id}", source_kind="notion", source_ref=page_id, audio_path=None, transcript_es_path=<written>, status=NoteStatus.TRANSCRIBED)`.
  - Inserts + yields.
- Pacing: reuse `pipeline.notion_client._PACE_SECONDS = 0.4` (already imported) — 29 pages × 0.4s = ~12s ceiling for listing.

### Why iterators, not classes

A class hierarchy implies polymorphic behavior beyond enumeration. There isn't any — both sources only enumerate. Telegram does have side-effects (file download, cursor advance) but those are private to the adapter, not the protocol. An `Iterator` keeps the interface a single verb.

---

## Processing Pipeline Shape — Plain Python, Not LangGraph

### The five stages

```
Source.iter_pending()  ──▶  for note in notes:
                              transcribe(note)      # skipped if note.audio_path is None
                              split(note)           # ES transcript → chunks_es
                              translate(note)       # chunks_es → chunks_en (parallel-safe per chunk)
                              extract(note)         # chunks_en → extraction_reports (parallel-safe per chunk)
                              present_for_review(note)  # writes Drafts + Telegram message; status=AWAITING_REVIEW
                              # ⇣ async pause: worker exits here
                              # ⇣ next worker tick, callback_query advances status to APPROVED/REJECTED
                              if note.status == APPROVED:
                                  commit_to_vault(note)
```

### Why NOT LangGraph for this module

| LangGraph buys you | Voicenote needs it? |
|---|---|
| Conditional fan-out / fan-in on a DAG | No. Flow is linear. |
| `interrupt()` + SqliteSaver resume after HITL pause | No. The pause is a Telegram callback_query that fires on the next worker tick; the worker reads the new draft `decision` from SQLite and resumes. No checkpoint serialization needed. |
| Per-node `RetryPolicy` with classified transient errors | Partial — but a 30-LOC retry helper around `call_llm` covers it. Pipeline's `_is_transient` classifier can be imported from `pipeline.graph` if needed. |
| Telemetry hooks via `add_node` | We can append to `voicenote/state/runs.jsonl` directly via the existing `pipeline.runtime.append_metric`. |
| Parallel branches | The only parallel work is per-chunk LLM calls (translate, extract). A small `concurrent.futures.ThreadPoolExecutor(max_workers=3)` does this in 15 LOC. |

**Cost of LangGraph for this:** every stage becomes a node function, every state field needs a TypedDict entry, the SqliteSaver doubles the persistence story (one DB for graph checkpoints + one DB for app state), and the `interrupt()` resume model fights the natural "cron tick reads SQLite and continues" pattern. The existing pipeline uses LangGraph because it has a real DAG with `kb_curator` ↔ `extract_image` ↔ `youtube_upload` parallelism and 6-hop HITL approval loops. Voicenote does not have those.

**Cost of plain Python:** must reimplement structured logging, per-stage telemetry, retry classification. All three are 1-file imports from `pipeline/`.

### Logging matches the pipeline conventions

- Stage prefix: `[voicenote.split]`, `[voicenote.translate]`, `[voicenote.review]`, `[voicenote.commit]`. Lowercase dotted, square-bracket — extension of the existing `[stage]` convention.
- Per-stage row appended to `processed/<voicenote_run_id>/runs.jsonl` via `pipeline.runtime.append_metric` (which already writes JSONL).
- Loud-fail on missing input: each processing function asserts `note.status == <expected_previous>` first, raises `RuntimeError` if not. This is the voicenote analog of `assert_inputs` from `pipeline.contracts`.

### Retry policy

- LLM calls: rely on `pipeline.llm.call_llm` which already has 4 retries with 4s base backoff + auth refresh on 401. No additional wrapping needed.
- Whisper subprocess: copy the CUDA→CPU fallback pattern from `pipeline/nodes/transcribe.py` lines 36-80. Same `extract_transcription.sh` invocation, just with `--lang es`.
- Notion read in `sources/notion.py`: `_PACE_SECONDS = 0.4` between page fetches; if `httpx.HTTPError` raised, bubble — worker will exit, next cron tick retries from same cursor (idempotent).
- Telegram getUpdates: a single `httpx.HTTPError` → log + exit rc=0 (do not surface a failed timer unit; the next tick recovers).

---

## Review State Machine

### States and transitions

```
LongNote.status flow:

PENDING ──▶ TRANSCRIBING ──▶ TRANSCRIBED ──▶ SPLITTING ──▶ SPLIT
                                                              │
                                                              ▼
                                              TRANSLATING ──▶ TRANSLATED ──▶ EXTRACTING ──▶ EXTRACTED
                                                                                                   │
                                                                                                   ▼
                                                                              AWAITING_REVIEW  ◀─ worker emits N drafts
                                                                                                   │
                                                                  ┌────────────────────────────────┤
                                                                  ▼                                ▼
                                                             APPROVED                           REJECTED
                                                                  │                                │
                                                                  ▼                                ▼
                                                             COMMITTING ──▶ COMMITTED       (terminal)
                                                                  │
                                                                  ▼ (on error)
                                                              FAILED  ──▶ replayable
```

### Where pending state lives — SQLite, single source of truth

- `long_notes.status` advances atomically per stage.
- `drafts.decision` ("pending" | "approved" | "rejected" | "edit_requested") drives the review verdict — one row per chunk.
- A note transitions `AWAITING_REVIEW → APPROVED` only when ALL its drafts have `decision='approved'`. If any draft is `rejected`, the note becomes `REJECTED` (or partially-committed depending on per-draft decision granularity — see "Decision granularity" below).
- Telegram `message_id` stored on each draft row → on user click we know which draft was just decided AND we can call `editMessageReplyMarkup` to remove the buttons so the user can't double-click.

### Idempotency on re-poll

- Telegram `update_id` cursor in `telegram_cursor` table → never re-process an update.
- `INSERT OR IGNORE` on `long_notes.(source_kind, source_ref)` → never re-create a LongNote for the same source message.
- Each callback_query carries a `data` field that we encode as `f"draft:{draft_id}:{decision}"`. On receipt, we `UPDATE drafts SET decision=:d WHERE id=:id AND decision='pending'` — the `AND decision='pending'` clause makes double-click a no-op.

### Decision granularity — per-draft, not per-note

A long note splits into N drafts. The user might approve 3 of 4 and reject 1 (the rejection might be "this is a duplicate of an entry from April"). Per-draft buttons mean partial commits — `commit_to_vault` writes only the approved drafts. The rejected ones live in the DB for audit but never reach the vault. This matches the user's "flag-don't-skip" policy AND lets them throw away clearly-redundant chunks without losing the others.

### The pause is naturally async — no event loop needed

When a worker tick gets to `AWAITING_REVIEW`, it exits (rc=0). It does NOT block waiting for user reply. The next cron tick:
1. Polls Telegram → may receive `callback_query` updates → routes to `review/handler.py` → updates draft decisions in SQLite.
2. Scans `long_notes WHERE status = 'AWAITING_REVIEW'` and checks if all child drafts have non-`pending` decisions.
3. For each note where the review is complete, advances status to `APPROVED` or `REJECTED` and (if approved) runs `commit_to_vault`.

This means worker ticks are completely stateless — they read SQLite, do one batch of work, write SQLite, exit. Watchdog story = same as daily-brief: "did `voicenote/state/voicenote.db` get updated in the last N hours, OR are there any rows stuck in `*_ING` state for > 1h?".

---

## Cron + Bot Integration

### Match the daily-brief pattern exactly

- **Timer:** `~/.config/systemd/user/painforwisdom-voicenote.timer` running every 5 min (configurable). Calls `painforwisdom-voicenote.service` which runs `python -m voicenote.worker --poll-once`.
- **Service:** `Restart=on-failure`, `RestartSec=60`, `StartLimitBurst=3`, `StartLimitIntervalSec=2h` — copy the daily-brief unit verbatim; if upstream Telegram goes 5xx for >2h, service is held in `failed` until either next cron-watchdog `reset-failed` or manual intervention.
- **Watchdog:** `pipeline/scripts/check_voicenote_freshness.sh` cron-driven (NOT systemd — for the same reason daily-brief uses cron: it survives the failure modes that kill systemd). Alerts if:
  1. `voicenote.db` `long_notes.updated_at` is older than `MAX_AGE_HOURS=12` AND there are pending Telegram messages, OR
  2. Any `long_notes.status LIKE '%_ING'` (i.e. mid-stage) for > 1 hour, OR
  3. `systemctl --user is-active painforwisdom-voicenote.timer` ≠ "active".
- **Heal-then-notify** mirrors the May 2026 daily-brief fix (commit `2e21bd9`): cron calls `systemctl --user reset-failed && start` before paging.
- **Dedupe state** for watchdog alerts under `~/.local/state/painforwisdom/voicenote_watchdog.last_alert` (outside repo working dir — fixes the `git clean -fdx`-wipes-it concern from `CONCERNS.md`).

### Why one-shot polling, not long-lived process

| Pattern | Idle cost | Failure mode | Deploy story | Match to existing ops |
|---|---|---|---|---|
| `Application.run_polling()` long-lived | 1 sleeping Python proc (negligible) | Orphaned getUpdates connection on segfault; needs restart | Stop/start the service unit | Adds a third long-lived process to the host |
| Cron-spawned `--poll-once` | Zero | Each tick is independent; a single crash affects 5 minutes of input | systemd already restarts on failure | **Identical to daily-brief, identical watchdog story** |

The cron-spawned approach is strictly the operational analog of the daily-brief — reuse the patterns the team has spent 5+ commits hardening.

### `python-telegram-bot` v22 — use the Bot class directly, skip `Application`

`Application.run_polling()` is designed for long-lived processes. For one-shot polling we want just the raw `Bot.get_updates()` call.

```python
# voicenote/sources/telegram.py — sketch
from telegram import Bot
from telegram.error import TelegramError

async def fetch_new_updates(bot: Bot, offset: int) -> list[Update]:
    return await bot.get_updates(offset=offset, timeout=0, allowed_updates=["message", "callback_query"])
```

PTB v22 is async-only. Worker runs `asyncio.run(main())`. This is the only place asyncio enters the codebase; the existing pipeline is sync. Acceptable because the alternative (subprocess curl calls) loses typed update parsing.

---

## kb-curator Hand-Off

### Direct in-process call, NOT a queue file

The existing `pipeline/nodes/kb_curator.py:node_kb_curator` does these things:
1. Loads `.claude/agents/kb-curator.md`.
2. Builds vault snapshot.
3. Calls `pipeline.llm.call_llm` with the extraction report + snapshot.
4. Parses `---kb-plan---` YAML.
5. Applies the plan: writes entry, updates themes, updates `_index.md`, rewrites `book-outline.md`.

For voicenote we want steps 1-5 BUT per approved draft, with custom inputs (the `parent_note` link for sibling entries, the `source:` frontmatter, the overlap-flag annotation).

**Recommendation: extract steps 4-5 into `pipeline/vault_writer.py` as a reusable function**, then both `pipeline/nodes/kb_curator.py` AND `voicenote/vault/writer.py` call it. Signature:

```python
def apply_curation_plan(plan: Dict[str, Any], *, parent_note: Optional[str] = None,
                       source_uri: Optional[str] = None, overlap_flag: Optional[str] = None) -> Path:
    """Writes the entry, updates touched themes/frameworks, appends _index.md row,
    rewrites book-outline.md. Returns entry path. Refuses overwrite of existing entry."""
```

This is a low-risk refactor — `_apply_proceed` is already a private helper inside `kb_curator.py`; lifting it is mostly a cut-paste with the new optional kwargs.

### The hand-off path for a single approved draft

```python
# voicenote/vault/writer.py — sketch
def commit_draft(draft: Draft, parent_note_id: str) -> Path:
    # The draft already carries entry_markdown that the worker generated by
    # running coaching-thought-extractor on the EN chunk. Now we curate.
    snapshot = build_vault_snapshot()        # reuse pipeline.nodes.kb_curator._vault_snapshot
    plan_yaml = call_llm(                    # reuse pipeline.llm.call_llm
        model=os.environ.get("PIPELINE_MODEL", "claude-sonnet-4-6"),
        system_prompt=load_agent_prompt("kb-curator.md"),
        user_message=_build_curator_msg(draft, snapshot),
        max_tokens=4000,
    )
    plan = parse_kb_plan(plan_yaml["text"])  # reuse the existing parser
    return apply_curation_plan(
        plan,
        parent_note=parent_note_id,
        source_uri=draft.source_uri,
        overlap_flag=draft.overlap_flag,
    )
```

### Submodule commit on success

Per the `[high]` `CONCERNS.md` item — the existing pipeline never commits the vault submodule. Voicenote inherits the same problem unless we fix it. **Recommendation: do the fix here.** After `apply_curation_plan` returns, `voicenote/vault/writer.py` shells:

```python
def commit_vault_submodule(entry_paths: List[Path], parent_note_id: str) -> None:
    vault_root = VAULT_PATH  # from pipeline.runtime
    rel_paths = [p.relative_to(vault_root) for p in entry_paths]
    subprocess.run(["git", "add", "--", *map(str, rel_paths)], cwd=vault_root, check=True)
    subprocess.run(
        ["git", "commit", "-m", f"voicenote: add {len(rel_paths)} entries from {parent_note_id}"],
        cwd=vault_root, check=True,
    )
    # Don't push automatically — leaves a clean review window. Document in OPERATIONS.md
    # that the operator runs `cd obsidian-vault && git push` after a session.
```

This closes the `[high]` vault-dirty bug for voicenote-created entries; the existing pipeline benefits the same way if you adopt the same helper there in a follow-up.

---

## Failure Handling

Match the existing pipeline's two-tier error model (loud-fail + classified retries):

| Failure | Surface | Persistence | Recovery |
|---|---|---|---|
| Whisper subprocess fails | Telegram alert with stderr tail (matches the `CONCERNS.md` `[high]` recommendation — capture proc.stderr instead of just rc) | `long_notes.status = FAILED`, `error = <truncated stderr>` | `python -m voicenote.replay --id <note_id>` re-runs from `TRANSCRIBING` |
| CUDA OOM | Telegram warning ("falling back to CPU, ~10× slower"); `whisper_device_fallback='cpu'` flag on the note | Note proceeds; status flag persisted | None — the run completes |
| LLM split / translate / extract fails | `call_llm` already retries 4× internally; remaining failures bubble | `long_notes.status = FAILED`, error captured | Replay CLI |
| Telegram send fails on review prompt | Non-fatal log to stdout; status stays `EXTRACTED` (not `AWAITING_REVIEW`) so next tick retries the send | None | Next worker tick re-sends |
| User rejects all drafts | Telegram confirmation ("note discarded"); status `REJECTED` | Note + drafts retained for audit; no vault write | None — terminal |
| Vault submodule commit fails (merge conflict?) | Telegram alert; status stays `COMMITTING` | Entries are already on disk in the submodule but uncommitted | Operator resolves manually; replay CLI advances status |
| Backfill page fetch from Notion fails | One row's worth of work skipped; logged; processed_pages entry NOT inserted | None | Next backfill run retries |
| Allow-list rejection | Silent — no Telegram reply | Nothing persisted | None (security feature, not error) |

### Dead-letter directory

`voicenote/state/dead_letter/` — when a note hits `FAILED`, copy `audio_path` + transcripts there so a `git clean -fdx` doesn't wipe failed-but-replayable input. Mirrors `to_be_retried/` from the existing pipeline.

### Telegram delivery hardening

Reuse the lessons from the 5+ recent Telegram-reliability commits:
- HTML escape any draft title or overlap-flag slug before embedding in review-prompt messages.
- On 400 from Telegram, fall back to plain-text + truncate to 4096 chars (Telegram message cap).
- Bot ping at worker startup — if the first `getMe` fails, exit rc=1 immediately, watchdog will surface it.

---

## Concurrency

| Workload | Cap | Source of truth |
|---|---|---|
| Notion REST (backfill page reads) | `1 / _PACE_SECONDS = 2.5 req/s` (already ≤ 3 req/s documented limit) | Reuse `pipeline.notion_client._PACE_SECONDS = 0.4` |
| Whisper local | **Sequential.** GPU has one slot. Parallel Whisper invocations will OOM. | Acquire a `voicenote/state/.whisper.lock` flock before invoking `extract_transcription.sh`; release after. Same lock for both `transcribe.py` and any future replay run. |
| LLM (translate, extract) | `ThreadPoolExecutor(max_workers=3)` per long-note, across its chunks | Sonnet 4.6 RPM via subscription is generous (Ultra tier); 3 concurrent calls per note is conservative — bounded by the chunk count anyway (typical 2-4) |
| LLM (cross-note) | Sequential — process one LongNote at a time per worker tick | The cron interval is 5 min; one note end-to-end on local Whisper + LLM ≈ 2-4 min on the canonical fixture. If a batch arrives during a long Whisper run, the next tick picks it up. |
| Backfill (29 subpages) | `ThreadPoolExecutor(max_workers=2)` capped by Notion pacing (2.5 req/s) AND Whisper sequentiality (text-only path skips Whisper, so this is purely LLM-bound) | Sequential text-only end-to-end per page ≈ 1-2 min × 29 = ~45 min single-thread. With 2-way LLM parallelism and the Notion-side serial fetch, ~25 min realistic. |

### Why "sequential per note" not "parallel notes"

A burst of 5 Telegram voice messages arriving back-to-back will pile up. The worker can:
- (a) Serialize: process one at a time; messages 2-5 wait one cron tick each. **Total wall-clock: 5 ticks (25 min) before all reviewed.**
- (b) Parallelize: spin up 5 threads, all hit Whisper. **CUDA OOM on note 2 → CPU fallback for 2-5 → wall-clock balloons.**

(a) wins. The cron interval IS the queue — `iter_pending()` returns the oldest unprocessed first; bursts drain across ticks. Telegram-side review prompts can pile up; user dispatches them at their pace.

### Whisper lock implementation

```python
# voicenote/processing/transcribe.py — sketch
import fcntl
from pathlib import Path

WHISPER_LOCK = Path("voicenote/state/.whisper.lock")

def transcribe_es(note: LongNote, repo: Repo) -> LongNote:
    WHISPER_LOCK.parent.mkdir(parents=True, exist_ok=True)
    with WHISPER_LOCK.open("w") as lockf:
        fcntl.flock(lockf, fcntl.LOCK_EX)  # blocks if another worker holds it
        # ... shell extract_transcription.sh ...
```

Important for the case where a manual `python -m voicenote.replay --id X` runs while the cron worker is also active.

---

## Build Order — Riskiest Assumption First

Per user memory `feedback_poc_before_migration.md`: slim PoC validating the riskiest assumption first.

### Ranked risks

1. **HIGH: Spanish Whisper quality on long-form voice notes.** If Spanish transcription is unusable, everything downstream is wasted. The memory-note `feedback_audio_overview_format.md` and the constraint "no paid transcription vendor" both pivot on this.
2. **HIGH: LLM-driven ES splitting is reliable.** Splitting a 10-minute monologue into 2-4 atomic thoughts is a novel agent task. If the LLM consistently over-splits or under-splits, the whole "one long note → multiple entries" thesis breaks.
3. **MEDIUM: Translation preserves nuance.** Decision says translate per-chunk after split (avoid "translation flattens before split"); is per-chunk EN sufficient context for `coaching-thought-extractor`?
4. **MEDIUM: Review UX has the right granularity.** Per-draft buttons vs per-note buttons; what does the user actually want to dispatch in 30 seconds on a phone?
5. **LOW: Notion backfill mechanics.** Existing `notion_client` knows the API; 29 pages is small; nothing new conceptually.
6. **LOW: SQLite state-machine plumbing.** Boring but well-understood.
7. **LOW: Cron + watchdog plumbing.** Pattern already proven by daily-brief.

### Recommended build sequence

**PoC Phase (≤ 1 week, validates the top 2 risks):**

1. **`voicenote/processing/transcribe.py` Spanish mode + 1 real long-note transcript.** Shell `extract_transcription.sh` with `--lang es` on a real ~10-min recording. Measure: WER on a sampled paragraph, time on GPU vs CPU.
2. **`voicenote/processing/split.py` — new agent prompt `voicenote-splitter.md`.** Run on the PoC transcript. Hand-judge: are the chunks atomic? Do they correspond to what the user would identify as distinct thoughts?
3. **Hand-drive translate + extract on each chunk** using `pipeline.llm.call_llm` + the existing `coaching-thought-extractor.md` prompt. Inspect outputs.
4. **GO/NO-GO gate:** is the chunk-then-translate-then-extract chain producing book-grade outputs comparable to the video-derived entries in the vault today? If no, the whole architecture pivots (different chunking strategy, different LLM, or different vendor).

**Build Phase 1 (validates the Telegram capture loop):**

5. `voicenote/models.py` + `voicenote/repo.py` + SQLite schema.
6. `voicenote/sources/telegram.py` + `voicenote/allowlist.py` + Telegram cursor persistence. Test: send a voice message, see it land in `long_notes` table.
7. `voicenote/worker.py` minimal — process one note end-to-end through transcribe + split + translate + extract; no review yet, just write the extraction reports to disk and notify Telegram.

**Build Phase 2 (review state machine):**

8. `voicenote/review/presenter.py` + `voicenote/review/handler.py` + drafts table. Test: receive voice message → get N review prompts → click Approve/Reject → see decisions persisted.

**Build Phase 3 (vault hand-off):**

9. Refactor `pipeline/nodes/kb_curator.py:_apply_proceed` → `pipeline/vault_writer.py:apply_curation_plan` (the only touch to existing pipeline code).
10. `voicenote/vault/writer.py` — wire approved drafts through the curator + submodule commit.

**Build Phase 4 (backfill):**

11. `voicenote/sources/notion.py` — list 29 subpages, dedupe via `processed_notion_pages`.
12. Backfill CLI: `python -m voicenote backfill --source notion --limit 5` (start small).

**Build Phase 5 (ops):**

13. systemd unit files + watchdog script.
14. `python -m voicenote status` CLI for human inspection of the state machine.

---

## Reuse vs. Build Matrix

| Component | Reuse As-Is | Refactor (light) | Build New |
|---|---|---|---|
| Whisper invocation (`extract_transcription.sh`) | ✓ | | |
| LLM wrapper (`pipeline/llm.py`) | ✓ | | |
| Agent-prompt loader (`pipeline.runtime.load_agent_prompt`) | ✓ | | |
| Per-stage telemetry (`pipeline.runtime.append_metric`) | ✓ | | |
| Notion REST client (`pipeline/notion_client.py`) | ✓ | | |
| `coaching-thought-extractor.md` agent prompt | ✓ | | |
| `kb-curator.md` agent prompt | ✓ | | |
| Telegram send (`pipeline/telegram.py`) | ✓ for outbound sends | Add `chat_id` plumbing for the new voicenote bot's chat | |
| Vault writer (`pipeline/nodes/kb_curator.py:_apply_proceed`) | | Lift into `pipeline/vault_writer.py:apply_curation_plan` with new kwargs | |
| Vault submodule commit | | Add new helper `commit_vault_submodule()` (close the `CONCERNS.md` `[high]` bug) | ✓ |
| `LongNote` dataclass + `NoteStatus` enum | | | ✓ |
| SQLite schema + DAL (`voicenote/repo.py`) | | | ✓ |
| Source protocol + `sources/telegram.py` + `sources/notion.py` | | | ✓ |
| `voicenote-splitter.md` agent prompt | | | ✓ |
| Spanish→English translator agent (prompt OR inline system message) | | | ✓ |
| Review state machine (`review/presenter.py`, `review/handler.py`) | | | ✓ |
| Worker entry point + cron + watchdog | Pattern from daily-brief | Adapt unit names + paths | ✓ |
| stdlib `unittest` tests | Conventions from `tests/` | | ✓ for new modules |

---

## Data Flow — One Diagram

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         systemd timer (every 5 min)                      │
│                                    │                                     │
│                                    ▼                                     │
│                  python -m voicenote.worker --poll-once                  │
└─────────────────────────────────────────────────────────────────────────┘
                                     │
        ┌────────────────────────────┼────────────────────────────┐
        ▼                            ▼                            ▼
 ┌────────────────┐         ┌────────────────┐         ┌────────────────────┐
 │ TelegramSource │         │  NotionSource  │         │  pending callback  │
 │  .iter_pending │         │  .iter_pending │         │  _query updates    │
 │  (new voices)  │         │  (one-shot)    │         │                    │
 └───────┬────────┘         └───────┬────────┘         └─────────┬──────────┘
         │                          │                            │
         ▼                          ▼                            ▼
 ┌────────────────────────────────────────────────────┐  ┌──────────────────┐
 │   INSERT OR IGNORE INTO long_notes                 │  │ review/handler   │
 │   (idempotent on source_ref)                       │  │ UPDATE drafts    │
 │                                                    │  │ SET decision=... │
 └────────────────────────────────────────────────────┘  └──────────────────┘
                          │                                       │
                          ▼                                       ▼
            ┌───────────────────────────────────────────────────────────────┐
            │              SQLite: voicenote/state/voicenote.db              │
            │  long_notes  ·  drafts  ·  telegram_cursor  ·  processed_pages │
            └────────────────────────────────┬──────────────────────────────┘
                                             │
                                             ▼
                  ┌──────────────────────────────────────────────┐
                  │ for note in pending(): advance one stage      │
                  │   transcribe → split → translate → extract    │
                  │   → present_for_review → ⏸ (exit worker)      │
                  │   on next tick: if all drafts decided →       │
                  │   commit_to_vault → submodule commit          │
                  └──────────────────────────────────────────────┘
                                             │
        ┌────────────────────────────────────┼─────────────────────────────────┐
        ▼                                    ▼                                 ▼
 ┌──────────────────┐              ┌──────────────────┐            ┌──────────────────┐
 │ pipeline.llm     │              │ extract_transcr- │            │ pipeline/        │
 │ .call_llm        │              │ iption.sh        │            │ vault_writer.py  │
 │ (translate,      │              │ (Whisper local,  │            │ apply_curation_  │
 │  extract,        │              │  lang=es,        │            │ plan + submodule │
 │  split,          │              │  CUDA→CPU fb)    │            │ commit           │
 │  kb-curator)     │              │                  │            │                  │
 └──────────────────┘              └──────────────────┘            └────────┬─────────┘
                                                                            │
                                                                            ▼
                                                              ┌────────────────────────┐
                                                              │ obsidian-vault/        │
                                                              │ gonzalo-book/entries/  │
                                                              │ <YYYY-MM-DD-slug>.md   │
                                                              │ (one per approved      │
                                                              │  chunk;                │
                                                              │  parent_note +         │
                                                              │  source frontmatter)   │
                                                              └────────────────────────┘
```

---

## Anti-Patterns to Avoid

### Anti-pattern 1: Adding voicenote stages to the existing LangGraph DAG

**What people do:** Wire a "voice" entry point into `pipeline/graph.py` that flows through transcribe → extract → kb_curator alongside the video path.
**Why it's wrong:** The pipeline's State TypedDict is video-shaped (`video_path`, `transcript_path`, `featured_image_path`, `youtube_url`). Bolting voice fields onto it conflates two distinct lifecycles. The video pipeline's HITL interrupt model serializes one item end-to-end; voicenote's review pause is parallelizable across many in-flight notes.
**Do this instead:** Keep the new module as a sibling package. Share helpers (LLM, agent prompts, vault writer), not the DAG.

### Anti-pattern 2: Storing review state in Telegram messages

**What people do:** Use message edits + reactions to encode "this draft is approved." Re-poll on each tick parses Telegram's view of the world.
**Why it's wrong:** Loses idempotency. A deleted message becomes a deleted state. A bot restart loses message_id ↔ draft_id mappings. The `_wait_reply` chat-id bug from `CONCERNS.md` is in the same family.
**Do this instead:** SQLite is the source of truth. Telegram messages are a render of it. `callback_data` is the only thing read back; everything else is one-way.

### Anti-pattern 3: Translating the whole transcript before splitting

**What people do:** ES transcript → translate to EN → split into atomic thoughts → extract each.
**Why it's wrong:** Translation flattens. The key tells (rhythm changes, side-trail markers like "bueno, pero…", emphasis particles) that signal thought boundaries in Spanish are normalized away by a competent translator. Splitting on the EN version after translation produces less-atomic chunks.
**Do this instead:** Split in Spanish first (the user's native phrasing). Translate each chunk. Extract on each EN chunk independently. This is already a Key Decision in PROJECT.md — reaffirming it here so the architecture surfaces match.

### Anti-pattern 4: Letting the existing pipeline's content-pipeline channel double as the voicenote review channel

**What people do:** Send voicenote review prompts to `TELEGRAM_CHAT_ID` (the existing content_pipeline channel).
**Why it's wrong:** The pipeline already uses that channel for kb_curator HITL approvals; mixing them lets a daily-brief reply unintentionally answer a voicenote review prompt (the `wait_reply` chat-id-filter bug from `CONCERNS.md` again).
**Do this instead:** Dedicated `TELEGRAM_VOICENOTE_CHAT_ID` env var. A separate bot token (PROJECT.md Key Decision). Distinct chat. Distinct allow-list verification.

### Anti-pattern 5: Auto-committing vault entries without push gate

**What people do:** Voicenote writes entry → `git add` → `git commit` → `git push` all in one tick.
**Why it's wrong:** A bad split or a hallucinated theme attachment ends up on `draft` branch before the user can sanity-check the day's batch. Even if approvals are per-draft, the vault is the canonical store — once pushed, rewriting history breaks downstream tools.
**Do this instead:** `git add` + `git commit` inside the submodule on each commit. **Defer push to a manual `git push` step** (or a daily systemd timer at end-of-day if the operator prefers it batched). This is consistent with the existing pipeline's vault hygiene direction (the `CONCERNS.md` `[high]` fix recommendation is "teach `kb_curator` to commit+push" — voicenote starts by doing commit-only; push can come later when reviewed).

---

## Integration Points

### External services

| Service | How voicenote integrates | Notes |
|---|---|---|
| Telegram Bot API | `python-telegram-bot` v22 `Bot` class only (no `Application`) for `get_updates` + file download + send + editMessageReplyMarkup. `pipeline.telegram.send` for outbound text. | New bot token, separate from the daily-brief bot. v22 is async-only — `asyncio.run()` per worker tick. |
| Anthropic LLM | `pipeline.llm.call_llm` exclusively. No direct litellm or Anthropic-SDK use. | Split, translate, extract, curate all funnel through one wrapper. |
| Local Whisper | `extract_transcription.sh --lang es <path.ogg>`. No conversion needed — Whisper handles `.ogg`. | Local Whisper supports OGG/Opus directly via ffmpeg internally. |
| Notion REST | `pipeline.notion_client.get_client()` + `pages.retrieve` + `blocks.children.list` for backfill. No new properties written. | Existing pacing constant. Read-only operations against Voicepal pages. |
| Obsidian vault (git submodule) | `pipeline.vault_writer.apply_curation_plan` + `subprocess.run(["git", "add", "commit"])` inside `obsidian-vault/`. | The push step stays manual for v1. |

### Internal boundaries

| Boundary | Communication | Notes |
|---|---|---|
| `voicenote/sources/*` ↔ `voicenote/worker.py` | `Iterator[LongNote]` protocol | Source agnostic |
| `voicenote/processing/*` ↔ `voicenote/repo.py` | Function calls; processing reads + persists status transitions in same transaction | No global state; each function takes `repo` parameter |
| `voicenote/review/*` ↔ `voicenote/repo.py` | Same — function calls + transactions | `presenter` reads drafts → sends; `handler` writes draft decisions |
| `voicenote/vault/writer.py` ↔ `pipeline/vault_writer.py` | Direct Python call into shared helper | Only voicenote → pipeline import; no reverse coupling |
| `voicenote/*` ↔ `pipeline/llm.py`, `pipeline/runtime.py`, `pipeline/notion_client.py`, `pipeline/telegram.py` | Direct Python imports | One-way; voicenote depends on pipeline (sibling), pipeline does NOT import voicenote |
| Worker ↔ another worker (concurrent ticks) | SQLite WAL mode + `.whisper.lock` flock | One DB connection per worker; concurrent reads safe via WAL |

---

## Scaling Considerations (single-user, so mostly N/A)

| Scale | Adjustment |
|---|---|
| 5-10 voice messages/day | Default 5-min cron tick handles trivially. SQLite under 1 MB indefinitely. |
| 50 messages/day burst | Bursts drain across ticks (5/tick × 12 ticks/hr = 60 throughput). No change needed. |
| 29-page backfill | Single dedicated run (~25-45 min). No change needed. |
| Multi-user (NOT in scope per PROJECT.md) | Would require: chat_id-keyed allow-list, per-user state isolation in DB, per-user vault target. Don't build for this. |

---

## Sources

- **Existing codebase analysis (HIGH confidence):**
  - `/home/gonzalo/workspace/painforwisdom/painforwisdom/.planning/codebase/ARCHITECTURE.md` — pipeline DAG, runtime, telemetry, error tiers
  - `/home/gonzalo/workspace/painforwisdom/painforwisdom/.planning/codebase/STRUCTURE.md` — directory layout patterns, naming, where-to-put conventions
  - `/home/gonzalo/workspace/painforwisdom/painforwisdom/.planning/codebase/CONVENTIONS.md` — loud-fail, prompt loading, error classification, stage logging
  - `/home/gonzalo/workspace/painforwisdom/painforwisdom/.planning/codebase/CONCERNS.md` — vault dirty submodule, chat-id filter gap, watchdog dedupe state, silent-feature-drop policy
  - `/home/gonzalo/workspace/painforwisdom/painforwisdom/.planning/PROJECT.md` — scope, out-of-scope, key decisions
  - `pipeline/notion_client.py` — Notion pacing, data source UUIDs, schema caching
  - `pipeline/llm.py` — call_llm signature, retry / auth refresh
  - `pipeline/runtime.py` — `load_agent_prompt`, `append_metric`, `VAULT_PATH` resolution
  - `pipeline/nodes/kb_curator.py` — vault write + curator plan parsing (lift candidate)
  - `pipeline/nodes/transcribe.py` — Whisper invocation + CUDA→CPU fallback pattern
  - `telegram_io.sh` — existing Telegram primitive, current `wait_reply` semantics
- **`python-telegram-bot` v22 (HIGH confidence, Context7):**
  - `/python-telegram-bot/python-telegram-bot` — `Bot.get_updates`, `PicklePersistence` (not used here; SQLite chosen instead), long-poll vs one-shot patterns
- **User memory (HIGH confidence — explicit prior decisions):**
  - `feedback_poc_before_migration.md` — drives the "validate Whisper-ES + LLM split first" recommendation
  - `feedback_no_silent_feature_drops.md` — drives the FAILED-state + replay CLI design
  - `feedback_audio_overview_format.md` — informs the "no paid vendor" constraint via the same mobile-only listening pattern

---

*Architecture research for voicenote module — 2026-05-18*
