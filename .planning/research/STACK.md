# Stack Research — Voicenote Module

**Domain:** Long-form Spanish voice-note → Obsidian/Notion coaching-thought pipeline (new `voicenote/` module sibling to existing `pipeline/`)
**Researched:** 2026-05-18
**Confidence:** HIGH (existing-stack reuse decisions); MEDIUM (Spanish Whisper variant pick — empirical WER on Gonzalo's voice still to be measured); HIGH (scheduling decision — matches in-repo precedent)

> **Audience for this file:** the `/gsd-new-project` roadmap writer. Each section is prescriptive — "use X because Y", not "options exist". Reuse-vs-new-dep is called out explicitly on every component because PROJECT.md constraint #1 reads "no new core dependencies unless explicitly required".

---

## TL;DR

| Component | Decision | Source |
|-----------|----------|--------|
| Telegram bot | **NEW dep: `python-telegram-bot[ext] ==22.7`** — async `Application`, long-poll `run_polling()`, `CallbackQueryHandler` for inline buttons. Do **not** extend `telegram_io.sh` (curl/bash) for an interactive bot. | New |
| Spanish transcription | **NEW backend in `extract_transcription.sh` (do NOT add new pip dep):** call existing local `whisper` binary with `--language Spanish --model large-v3` for long voice notes; keep current `medium` default for video transcripts. Re-evaluate after backfill measures real WER. | Reuse + flag change |
| LLM splitter (long ES → atomic thoughts) | **Reuse `pipeline.llm.call_llm`** with a new prompt + JSON-schema output. No text-splitter library. Anthropic Sonnet 4.6 with structured `tool_use` output is more reliable than regex/heuristic splitters on conversational Spanish. | Reuse |
| Translation (ES → EN per chunk) | **Reuse `pipeline.llm.call_llm`** — same Anthropic Sonnet 4.6 path. No DeepL / no MarianMT / no separate translation library. | Reuse |
| Telegram review UX | **`InlineKeyboardMarkup` + `CallbackQueryHandler` + per-draft `ConversationHandler`** state for approve / edit / reject of N drafts. | New (part of `python-telegram-bot`) |
| Scheduling (cron poll) | **systemd user timer** (new unit `painforwisdom-voicenote-poll.{service,timer}`) — matches existing `painforwisdom-daily-brief.{service,timer}` pattern. **Not** APScheduler, **not** crontab. | Reuse pattern |
| Audio storage | **Plain filesystem + `.gitignore`:** `voicenote/audio/YYYY-MM-DD-<slug>.ogg` next to existing `processed/` layout. No new lib. Frontmatter on the entry carries `source: telegram://msg/<id>`. | Reuse pattern |
| Idempotency / poll cursor | **SQLite single-file DB at `voicenote/state/voicenote.db`** (cursor: last `update_id`; processed: `set<notion_page_id>`; pending: drafts awaiting Telegram confirm). Stdlib `sqlite3` — no ORM. | Reuse pattern |
| State machine for review flow | **LangGraph `interrupt()` + `SqliteSaver`** in `voicenote/checkpoints.db` (separate file from `pipeline/checkpoints.db`). Resume on Telegram callback. Matches the existing kb-curator HITL pattern. | Reuse |

---

## Recommended Stack

### Core Technologies (reuse from existing `pipeline/`)

| Technology | Version | Purpose | Why Recommended |
|------------|---------|---------|-----------------|
| Python | 3.11 (Conda env `painforwisdom-poc`) | Runtime | Existing env. No reason to fork. |
| LangGraph | `>=0.6,<0.7` | DAG + HITL `interrupt()` + checkpointer | Already powering `pipeline/`. The review-before-commit flow is structurally identical to kb-curator's Telegram approval — same primitive (`interrupt()` + `Command.resume`) reused. |
| langgraph-checkpoint-sqlite | `>=2.0,<3` | `SqliteSaver` for the voicenote review-loop graph | Same primitive. New DB file `voicenote/checkpoints.db` (do NOT share `pipeline/checkpoints.db` — different thread-id space, separate retention). |
| litellm | `>=1.55.0,<2` | LLM splitter + translator | Already wired (`pipeline/llm.py`); inherits OAuth subscription rotation, 1M-context beta gating, cost telemetry, retry/auth-refresh loop. Re-implementing these in `voicenote/` would duplicate ~300 LOC. |
| anthropic | `>=0.40.0,<1` | Type compat (LiteLLM path is the actual caller) | Already present. |
| notion-client | `>=2.2.1,<3` | One-shot Voicepal backfill: read 29 subpages | Already wired (`pipeline/notion_client.py`). Voicepal pages parent: `35b5901b-efa9-80d3-bb58-c1a5fc1ce7b3`. **Pacing already enforced (0.4s/req).** |
| python-dotenv | `>=1.0.0,<2` | Profile-aware `.env` load (prod vs sandbox) | Already wired; voicenote module follows the same `--profile` peek pattern from `pipeline/run.py`. |
| PyYAML | `>=6.0,<7` | Frontmatter parsing on vault entries (read-only) | Already in deps. |
| Local OpenAI Whisper | conda binary at `${HOME}/miniconda3/envs/painforwisdom/bin/whisper` | Transcribe `.ogg` voice files | Already wired (`extract_transcription.sh`). See "Spanish Whisper" section for model-size decision. |

### New Dependency (one, and only one)

| Library | Version | Purpose | Why Recommended |
|---------|---------|---------|-----------------|
| **python-telegram-bot[ext]** | `==22.7` (released 2026-03-16, latest stable as of 2026-05-18) | Async bot framework: `Application`, `MessageHandler(filters.VOICE)`, `CallbackQueryHandler`, `ConversationHandler` | The existing `telegram_io.sh` is a one-shot `curl`-on-`sendMessage`-and-`getUpdates` wrapper sufficient for fire-and-forget notifications and a single text Q/A. It does NOT support: persistent long-poll connection with handler dispatch, inline keyboards with callbacks, file downloads via `get_file()`. Re-implementing those over curl is anti-roadmap (≈600 LOC of state machine, error recovery, and parse-mode handling that PTB already ships). PROJECT.md claimed PTB is "already in deps" — **it is not** (verified against `pipeline/requirements.txt`). This is the one explicit new-dep call. |

**`[ext]` extra is mandatory:** it pulls `httpx` and `apscheduler` transitively, but we're using PTB's own JobQueue for nothing — only the `Application` framework. `httpx` is already in our deps. The transitive `apscheduler` arrives unused; that's fine.

### Supporting Libraries (already in deps — no install needed)

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| httpx | `>=0.27,<1` | HTTP client (Notion reads, file downloads if needed) | Reused via existing `pipeline/notion_client.py` and `pipeline/wordpress_client.py`. |
| stdlib `sqlite3` | — | Voicenote idempotency DB: last update_id, processed notion page IDs, pending drafts | One file (`voicenote/state/voicenote.db`). Schema migrated by `voicenote/scripts/init_db.py`. No ORM — matches `pipeline/themes_db.py` style. |
| stdlib `subprocess` | — | Shell out to `extract_transcription.sh` and (optionally) `git -C obsidian-vault commit` | Matches `pipeline/telegram.py` and `pipeline/zlibrary_bridge.py` patterns. |
| stdlib `pathlib`, `dataclasses`, `argparse`, `json`, `csv` | — | Per repo conventions (`pipeline/local_books.py`, `pipeline/themes_db.py`) | Matches `CONVENTIONS.md` rules — `dataclass` for value types, `TypedDict` for LangGraph state, no Pydantic. |
| stdlib `unittest` | — | Tests | Repo uses plain `pytest`-style files in `tests/` but with stdlib `unittest` (no `pytest` pin). New tests go in `tests/test_voicenote_*.py`. |

### Development Tools (no new tools)

| Tool | Purpose | Notes |
|------|---------|-------|
| `tests/smoke_pipeline.sh` style smoke harness | End-to-end voicenote smoke | New shell wrapper `tests/smoke_voicenote.sh` should follow same shape: sandbox profile, fixture voice file, assert vault entry. |
| `tests/sandbox_reset.sh` | Sandbox reset between smoke runs | Extend to also wipe `obsidian-vault-sandbox/voicenote/audio/` and the sandbox `voicenote.db`. |
| `journalctl --user -u painforwisdom-voicenote-poll.service` | Live log inspection | Matches daily-brief unit. |

---

## Installation

```bash
# Activate existing env
conda activate painforwisdom-poc
cd ~/workspace/painforwisdom/painforwisdom

# ONE new dep, added to pipeline/requirements.txt as a new section:
#   # Voicenote (new module): Telegram bot framework for voice intake + review UX.
#   python-telegram-bot[ext]>=22.7,<23
pip install 'python-telegram-bot[ext]>=22.7,<23'

# Verify Whisper conda env supports large-v3 (already installed; no upgrade needed).
${HOME}/miniconda3/envs/painforwisdom/bin/whisper --help | grep -A2 'large-v3'

# No other installs. All other capabilities are reused.
```

---

## Spanish Whisper — Detailed Decision

**Constraint (PROJECT.md):** local Whisper only, no API. **Existing default:** `WHISPER_MODEL=medium` on the conda binary, CPU fallback on CUDA OOM (`extract_transcription.sh:28`).

**Decision:** **Keep the same `whisper` binary; bump model to `large-v3` ONLY for voicenote runs** (via env `WHISPER_MODEL=large-v3 ./extract_transcription.sh ...`). Do **NOT** add `faster-whisper` as a pip dep in v1.

**Rationale:**

1. **`large-v3` is the published baseline for Spanish.** Spanish WER on conversational audio is reported at ~6-7% with `whisper-large-v3` (HF fine-tuned variants reach ~5.3%), vs ~9-11% for `medium`. For ~5-15 min voice notes that will be split into 2-4 thoughts each, errors in the middle of a thought boundary derail the splitter; the WER reduction is worth the runtime cost.
2. **`medium` stays the default for video** because:
   - Video transcripts are already English (`extract_transcription.sh` default `LANGUAGE=English`).
   - `medium` already cleared the existing 0.5/-1.0/2.4 confidence gates for video runs.
   - The 5 GiB VRAM ceiling on `medium` is documented as the "after OOM" default (`extract_transcription.sh:27-28`). `large-v3` is ~10 GiB.
3. **CPU fallback works.** Existing `WHISPER_DEVICE=cpu` flow handles CUDA OOM; expected runtime on CPU for `large-v3` on a 10-min audio is ~25-35 min — acceptable per the "5-15 min cron poll is fine" constraint. GPU runtime: ~1-3 min.
4. **`faster-whisper` deferred to v2.** It's 2-4× faster with INT8 quantization on the same large-v3 weights and would be a clean drop-in (single pip dep), but:
   - It changes the call surface from CLI subprocess → in-process Python. The existing `extract_transcription.sh` confidence gating (no_speech_prob > 0.5, avg_logprob < -1.0, compression_ratio > 2.4, ≥20% bad segments → auto-quarantine) is bash-side, parsing the CLI JSON. Porting that to Python is a separate concern.
   - faster-whisper does not change WER — only runtime — and the constraint is not real-time.
   - It does enable better VAD (`vad_filter=True`) which would help on long, pause-heavy voice notes — flag as a v2 upgrade.
5. **`whisperx` (diarization) NOT needed.** Single speaker (Gonzalo). Diarization adds complexity, model download, and a `pyannote` dependency that pulls `torch` + `torchaudio` model assets. Defer indefinitely unless a multi-voice source appears.

**Expected Spanish WER on the Voicepal corpus (estimate, MEDIUM confidence):**

| Model | Estimated WER | GPU time / 10 min audio | CPU time / 10 min audio |
|-------|---------------|--------------------------|--------------------------|
| `medium` (current default) | 9-11% | ~30-45 s | ~5-8 min |
| `large-v3` (recommended) | 5-7% | ~1-3 min | ~25-35 min |
| `large-v3-turbo` | 6-8% (~0.2 pp behind large-v3) | ~20-40 s | ~8-12 min |

`large-v3-turbo` is tempting — same WER neighborhood, much faster — but the existing conda env may not ship the turbo weights. **Action for the roadmap:** Phase 1 first task is `whisper --model large-v3-turbo --language Spanish` smoke; if available, prefer turbo. If not, use `large-v3`.

**OGG/Opus handling:** Telegram voice messages are OGG/Opus by default. `ffmpeg` (already on PATH, used by `extract_transcription.sh:72`) re-encodes them to 16 kHz mono MP3 for Whisper. No new audio lib needed.

---

## Telegram Bot — Detailed Decision

**Existing reality (`pipeline/telegram.py` + `telegram_io.sh`):** curl POST to `sendMessage`, curl long-poll `getUpdates` with `timeout=30` for replies, parse-mode env-overridable. Used for fire-and-forget alerts + one-shot Q/A. Caller env vars override `.env` so the daily-brief can route to a separate chat.

**Why we cannot reuse this for voicenote intake:**

1. **Long-poll lifecycle.** `telegram_io.sh wait_reply` is single-shot — it captures the next reply, then exits. The voicenote bot needs a long-running daemon (or short-lived cron-driven daemon) that handles arbitrary voice-message events, not just replies.
2. **Voice file download.** `getUpdates` returns a `voice.file_id`; downloading it requires `getFile` + `https://api.telegram.org/file/bot<TOKEN>/<file_path>`. Doable in bash, but adds significant complexity.
3. **Callback queries.** Inline keyboard button presses arrive as `callback_query` events, not `message` events. The existing curl wrapper has no dispatch logic.
4. **State preservation across drafts.** For "here are 3 drafts — approve/edit/reject each", the bot must remember which `callback_data` maps to which draft for which `message_id` — a state machine that PTB's `ConversationHandler` ships out-of-the-box.

**Why python-telegram-bot v22.7:**

- **Latest stable as of 2026-05-18.** Released 2026-03-16 ([changelog](https://docs.python-telegram-bot.org/en/stable/changelog.html)).
- **Async-only since v20.** The async `Application.run_polling(poll_interval=0.0, timeout=10)` is the native long-poll path. Idiomatic, well-documented.
- **`MessageHandler(filters.VOICE)`** + **`update.message.voice.get_file()`** + **`new_file.download_to_drive(path)`** is the one-liner voice ingestion path.
- **`CallbackQueryHandler(button_cb, pattern=r"^approve:")`** dispatches inline-button events by `callback_data` regex prefix.
- **`ConversationHandler`** handles the multi-step "edit this draft" flow (state: REVIEW → EDIT_DRAFT_1 → CONFIRM_EDIT) cleanly.
- **`Application.builder().token(...).persistence(...).build()`** lets us hand-roll persistence if needed (we won't — short-lived poll job + SQLite cursor is simpler).

**Bot architecture (informs Phase ordering in roadmap):**

The voicenote bot should run as a **short-lived periodic daemon** (systemd timer, every 5-15 min, `RuntimeWarning: ApplicationBuilder().build()` short-poll mode with `run_polling(stop_signals=None)` and an explicit `Application.stop()` after one drain cycle). NOT a permanent daemon, because:

- Matches the existing "schedule via systemd timer, not a daemon" pattern (see `painforwisdom-daily-brief.timer`).
- Crash recovery is automatic — next timer fire just resumes from last `update_id`.
- No supervisor needed.

**Alternative considered: persistent daemon.** Continuous `run_polling()` would give sub-second latency but introduces a 24/7 process, watchdog needs, and a different ops model than the rest of the repo. PROJECT.md explicitly says "5-15 min cron poll is fine" → kill the persistent daemon idea.

**Allowlist enforcement:**

```python
ALLOWED_USER_ID = int(os.environ["VOICENOTE_ALLOWED_USER_ID"])

async def gate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    if update.effective_user is None or update.effective_user.id != ALLOWED_USER_ID:
        # Silent reject per PROJECT.md constraint. No reply, no log spam — but
        # do append_metric() for visibility.
        return False
    return True
```

Apply via a top-level `TypeHandler(Update, gate)` with `block=True` so subsequent handlers see only allowed updates. This closes the `_wait_reply` chat-id gap CONCERNS.md flagged.

---

## LLM Splitter — Detailed Decision

**Task:** turn a 5-15 min Spanish transcript (typically 800-2500 words) into N atomic coaching thoughts (typically 2-4), each preserving original Spanish wording.

**Decision:** **Pure prompt-based with structured JSON output via `pipeline.llm.call_llm`.** No new library.

**Why not LangChain text splitters:**

- `RecursiveCharacterTextSplitter`, `SemanticChunker`, etc. are blind chunkers — they cut by token count or embedding similarity. Coaching thoughts have **semantic boundaries** ("y entonces me di cuenta", "esto me lleva a otro punto", topic shifts) that an LLM detects with high precision and length-based splitters mangle.
- LangChain pulls in ~50 transitive deps. We're not using LangChain anywhere else (LangGraph ≠ LangChain). Avoid the bloat.

**Why not regex/heuristic + LLM-verify:**

- Heuristic-first introduces failure modes the LLM-only version doesn't have ("paragraph break is not a thought break"). The LLM is good at this in one pass.

**Why not function-calling / Anthropic `tool_use` for structured output:**

- We *will* use structured output, but via the **same `tool_use` pattern Anthropic supports natively** through LiteLLM. `pipeline/llm.py` already plumbs `tools=[...]` through to LiteLLM (see signature `tools: Optional[List[Dict[str, Any]]] = None`). The splitter prompt declares a single tool `emit_thoughts` with a JSON Schema accepting `thoughts: list[{title_es: str, text_es: str, rough_themes: list[str]}]`. The model invokes the tool; LiteLLM returns `tool_use` blocks; we parse them. This is the same pattern the research node uses for `web_search_20250305`.
- Splitter prompt lives at `.claude/agents/voicenote-splitter.md` (matches convention; `load_agent_prompt` strips frontmatter + `## OUTPUT`).

**Spanish nuance preservation:**

- Splitter operates on **Spanish text**, emits **Spanish chunks**. Translation is a separate downstream call (per-chunk). This matches PROJECT.md key decision "transcribe → split (ES) → translate per chunk → extract per chunk" and avoids the "translation flattens before split" failure.
- Translation prompt also reuses `call_llm` — same Anthropic Sonnet 4.6, no DeepL/no MarianMT.

**Long-context awareness:**

- 5-15 min audio ≈ 800-2500 Spanish words ≈ 1200-3500 tokens of input. Well under Sonnet 4.6's standard 200k ctx. The `long_context=True` 1M beta header is NOT needed (and would burn quota — the recent commit `19e0e6e` explicitly per-call gated this). Splitter call uses standard context.

---

## Scheduling — Detailed Decision

**Existing precedent (`OPERATIONS.md:189`, INTEGRATIONS.md "CI/CD & Deployment"):**

> `crontab -l` is empty by design. All scheduled work uses systemd user units: `painforwisdom-daily-brief.{service,timer}` at `~/.config/systemd/user/`.

**Decision:** **NEW systemd user unit** `painforwisdom-voicenote-poll.{service,timer}` invoking `python -m voicenote.poll --apply`. Polls every 10 min (`OnCalendar=*:0/10`). Retry budget mirrors daily-brief: `Restart=on-failure / RestartSec=300 / StartLimitBurst=3 / StartLimitIntervalSec=2h`.

**Why not APScheduler:**

- Requires a persistent process to host the scheduler. We don't want one (see daemon discussion above).
- Adds a dep (`apscheduler`) and a state-store concern (persisting next-run-time across restarts).
- Doesn't compose with `journalctl` for unified logging.

**Why not crontab:**

- Repo convention is empty crontab + systemd timers (already documented).
- One exception: the existing `pipeline/scripts/check_daily_brief_freshness.sh` watchdog *is* in cron, deliberately, so it survives a systemd-user failure. The voicenote poll itself is NOT a watchdog — it's the actual workload — so systemd is correct.
- **DO add a watchdog cron** mirroring `check_daily_brief_freshness.sh`: `pipeline/scripts/check_voicenote_freshness.sh` alerts via Telegram if the voicenote timer hasn't fired in `MAX_AGE_HOURS=2`.

**Unit file template (write into roadmap):**

```ini
# ~/.config/systemd/user/painforwisdom-voicenote-poll.service
[Unit]
Description=painforwisdom voicenote poll (Telegram + Notion drain)
After=network-online.target

[Service]
Type=oneshot
WorkingDirectory=/home/gonzalo/workspace/painforwisdom/painforwisdom
ExecStart=/home/gonzalo/miniconda3/envs/painforwisdom-poc/bin/python -m voicenote.poll --apply
Environment="PATH=/home/gonzalo/miniconda3/envs/painforwisdom-poc/bin:/usr/bin:/bin"
Restart=on-failure
RestartSec=300
StartLimitBurst=3
StartLimitIntervalSec=2h

# ~/.config/systemd/user/painforwisdom-voicenote-poll.timer
[Unit]
Description=Poll Telegram + Notion for new voicenotes every 10 minutes

[Timer]
OnCalendar=*:0/10
Persistent=true

[Install]
WantedBy=timers.target
```

---

## Audio Storage Layout

**Decision:** filesystem-only, gitignored, indexed via frontmatter on the vault entry.

```
painforwisdom/
├── voicenote/
│   ├── audio/                                # gitignored
│   │   ├── 2026-05-18-storm-as-perfect-test.ogg
│   │   ├── 2026-05-18-pain-currency.ogg
│   │   └── ...
│   ├── state/
│   │   └── voicenote.db                       # gitignored; sqlite cursor + idempotency
│   ├── checkpoints.db                          # gitignored; LangGraph SqliteSaver for review-flow HITL
│   ├── audio/.gitkeep                           # so the dir survives a fresh clone
│   ├── sources/
│   │   ├── telegram.py
│   │   └── notion.py
│   ├── graph.py
│   ├── nodes/
│   │   ├── split_es.py
│   │   ├── translate.py
│   │   └── review.py
│   ├── poll.py                                  # entry point: `python -m voicenote.poll`
│   ├── bot.py                                   # entry point: `python -m voicenote.bot` (manual debug)
│   └── state.py                                 # TypedDict state schema (matches pipeline/state.py)
```

**Frontmatter contract (matches vault `entries/*.md` convention but with two new keys):**

```yaml
---
date: 2026-05-18
slug: storm-as-perfect-test
source: telegram://msg/12345          # or notion://page/35b5901befa980d3bb58c1a5fc1ce7b3
parent_note: telegram://msg/12340     # links siblings split from the same long note
audio: voicenote/audio/2026-05-18-storm-as-perfect-test.ogg  # gitignored; replayable
language_source: es
language_entry: en
---
```

**`.gitignore` additions** (write into roadmap as a Phase 1 task):

```
voicenote/audio/
voicenote/state/
voicenote/checkpoints.db
voicenote/checkpoints-sandbox.db
```

**No new library for "audio metadata".** The audio is opaque blob storage — we don't read EXIF/ID3 from `.ogg`. `mutagen` / `pydub` / `tinytag` are unnecessary. The vault entry's frontmatter is the index.

---

## Alternatives Considered

| Recommended | Alternative | When to Use Alternative |
|-------------|-------------|-------------------------|
| `python-telegram-bot[ext]==22.7` | `aiogram 3.x` | If the bot needed sub-100ms reply latency and finer-grained handler ergonomics. We don't — and aiogram has no advantage that justifies departing from the most popular and best-documented PTB. |
| `python-telegram-bot[ext]==22.7` | `pyTelegramBotAPI` (sync) | If we wanted to avoid asyncio. We don't — async is standard in 2026 and PTB's sync API was deprecated in v20 (2023). |
| `python-telegram-bot[ext]==22.7` | Extend `telegram_io.sh` (curl) | If the surface were "send one message and wait for one reply" — which is what the existing wrapper does and what kb-curator HITL uses. For interactive bot with inline buttons + voice download, curl-shimming is anti-architecture. |
| Local Whisper `large-v3` | Local Whisper `large-v3-turbo` | If turbo weights are present in the conda env. **Phase 1 first task should verify.** Same WER, ~5× faster. |
| Local Whisper `large-v3` | `faster-whisper >= 1.1` (pip) | Phase 2+. Cleaner Python API, 2-4× speedup with INT8, native VAD. Requires porting confidence-gating from bash to Python. |
| Local Whisper `large-v3` | `whisperx` | Multi-speaker source. Single-speaker (Gonzalo) → don't bother. |
| Local Whisper `large-v3` | Deepgram / AssemblyAI / OpenAI Whisper API | Explicitly out-of-scope (PROJECT.md). |
| systemd user timer | APScheduler in-process | If we ever consolidate to a single long-running daemon. Repo direction is the opposite. |
| systemd user timer | crontab | Repo convention says empty crontab. Watchdog stays in cron (one exception). |
| LangGraph `interrupt()` review loop | A standalone Python state machine in `voicenote/review.py` | If we wanted to avoid LangGraph for the new module. But kb-curator already proves `interrupt()` + Telegram resume works — duplicating that machinery in plain Python is wasted effort. |
| stdlib `sqlite3` for cursor / dedup | `sqlmodel`, `peewee`, `sqlalchemy` | Repo convention is stdlib sqlite (`pipeline/themes_db.py`, `pipeline/state/themes.db`). No ORM. |
| Anthropic Sonnet 4.6 via LiteLLM | OpenAI GPT-4o / Gemini 2.x for splitter | Constraint #4 (no other LLM vendors). Existing OAuth subscription path is the cost-zero choice. |
| Frontmatter `source: telegram://msg/<id>` | Notion staging DB | PROJECT.md Out-of-Scope explicitly bans the staging-DB intermediate. |

---

## What NOT to Use

| Avoid | Why | Use Instead |
|-------|-----|-------------|
| `pyTelegramBotAPI` / `telebot` | Sync-only ergonomics, smaller community, fewer docs. | `python-telegram-bot[ext]` (async). |
| Persistent Telegram daemon (`run_polling()` forever) | Adds 24/7 process supervision, breaks the "scheduled work via systemd timer" repo pattern, no recovery story for the embedded asyncio loop. | Short-lived systemd-timer-invoked drain cycle. |
| OpenAI Whisper API (REST) | Explicitly out-of-scope per PROJECT.md (paid vendor). The existing `openai-whisper-api` helper fallback should NOT be configured for voicenote. | Local Whisper conda binary. |
| Whisper `medium` for Spanish voice notes | WER on Spanish conversational ~9-11% — too low for clean splitter input on 800-2500-word transcripts. | `large-v3` (or `large-v3-turbo` if available). |
| LangChain text splitters | Length/embedding-based splitters miss semantic thought boundaries; pulls 50+ transitive deps. | LLM-based splitter via `pipeline.llm.call_llm` with structured tool-use output. |
| LangChain (the package) | Not used anywhere else in repo. LangGraph is a separate package and is already wired. | Direct LiteLLM calls. |
| Pydantic | Repo convention is `TypedDict` + `dataclasses` only. | `TypedDict(total=False)` for LangGraph state, `@dataclass` for value types. |
| `apscheduler` (direct usage) | Requires a persistent host process and parallel state for "what should have fired while I was down". | systemd timer + `Persistent=true`. |
| ORM (`sqlalchemy`, `sqlmodel`, `peewee`) | Repo convention is stdlib `sqlite3` with hand-written DDL. | stdlib `sqlite3`. |
| `pydub`, `mutagen`, `tinytag` (audio metadata libs) | `.ogg` is opaque blob storage; metadata lives in vault entry frontmatter. | Skip. |
| `python-telegram-bot[all]` (vs `[ext]`) | `[all]` pulls cryptography, socks proxy support, optional rate-limiter — none needed. | `[ext]` is the minimum for `Application`/`JobQueue` (we use `Application` only). Or even bare `python-telegram-bot` is enough — `[ext]` only adds `apscheduler` which we don't use. **Actually: bare `python-telegram-bot==22.7` is sufficient.** |
| Re-implementing OAuth subscription rotation, 1M-context gating, cost telemetry in voicenote | All of these are battle-tested in `pipeline/llm.py`, `pipeline/runtime.py`, `pipeline/cost_forecast.py`. | Import and call. |
| Per-entry git branches/PRs in `obsidian-vault` submodule | Explicit Out-of-Scope per PROJECT.md. | Direct commit on `draft` branch via existing pattern (`kb-curator` already does this). |

**Revision on PTB extras:** start with `python-telegram-bot==22.7` (no extras). Add `[socks]` only if Telegram becomes unreachable from the host network.

---

## Stack Patterns by Variant

**If GPU is available and `large-v3-turbo` is in the conda env:**
- Use `WHISPER_MODEL=large-v3-turbo` for voicenote runs (~3-5× faster than `large-v3`, same WER neighborhood).

**If GPU is OOM / unavailable:**
- Fall back to `WHISPER_MODEL=large-v3 WHISPER_DEVICE=cpu`. Expected 25-35 min per 10-min audio. The 10-min poll cadence will simply skip overlapping runs (the systemd unit is `Type=oneshot` and won't double-start).
- If CPU-only becomes painful (>1h backlog), Phase 2 should evaluate `faster-whisper` with INT8 quantization on CPU (real speedup: ~3-4×).

**If Voicepal backfill detects EN-titled-but-ES-bodied pages:**
- The splitter prompt should detect language per chunk (LLM is fine at this) rather than relying on page title.
- Frontmatter `language_source` should be set per chunk.

**If a voice message arrives > 20 MB (Telegram Bot API hard limit):**
- Bot replies with a "split into shorter messages" instruction. **Local-Bot-API-server workaround is Out-of-Scope** — adds a self-hosted Telegram Bot API server dep.

---

## Version Compatibility

| Package A | Compatible With | Notes |
|-----------|-----------------|-------|
| python-telegram-bot==22.7 | Python 3.9+ | Our env is 3.11 — clean. |
| python-telegram-bot==22.7 | httpx>=0.27,<1 | Transitive constraint matches our existing pin. No conflict. |
| python-telegram-bot==22.7 | anyio>=3 | Pulled transitively. No conflict expected. |
| python-telegram-bot==22.7 | langgraph>=0.6 | Both use asyncio. LangGraph supports sync invocation from async code via `graph.ainvoke`; the voicenote bot's handler will `await graph.ainvoke(state, config)` to drive review-flow `interrupt()`s. |
| litellm>=1.55,<2 | Anthropic API (subscription + API key) | Already wired; voicenote inherits without change. |
| Local whisper conda binary | `large-v3` weights | The `large-v3` model weights download on first invocation (~3 GB). Verify disk space before Phase 1 runs. |
| Local whisper conda binary | `large-v3-turbo` weights | May require `pip install -U openai-whisper>=20240930` in the conda env. **Phase 1 task should verify version.** |

---

## Sources

- `/python-telegram-bot/python-telegram-bot` — Context7 (Code Snippets: 659, Source Reputation: High, Benchmark 85.64, version v22.5) — verified `Application.run_polling`, `CallbackQueryHandler`, `MessageHandler(filters.VOICE)`, `get_file()`/`download_to_drive()` APIs are current and stable. HIGH confidence.
- [python-telegram-bot v22.7 stable docs](https://docs.python-telegram-bot.org/en/stable/index.html) — confirmed v22.7 is latest stable (released 2026-03-16). HIGH confidence.
- [python-telegram-bot releases on GitHub](https://github.com/python-telegram-bot/python-telegram-bot/releases) — release cadence + changelog. HIGH confidence.
- [Working with Files and Media (PTB wiki)](https://github.com/python-telegram-bot/python-telegram-bot/wiki/Working-with-Files-and-Media) — voice download pattern. HIGH confidence.
- [InlineKeyboard Example (PTB wiki)](https://github.com/python-telegram-bot/python-telegram-bot/wiki/InlineKeyboard-Example) — approve/reject UX pattern. HIGH confidence.
- `/systran/faster-whisper` — Context7 (Code Snippets: 70, Benchmark 86.84) — verified `WhisperModel("large-v3", device="cuda", compute_type="float16")` API, VAD parameters. HIGH confidence on API. **Deferred to Phase 2.**
- [Whisper Large V3 Turbo: As Good as Large V2 but 6x Faster (Medium, 2024)](https://medium.com/@bnjmn_marie/whisper-large-v3-turbo-as-good-as-large-v2-but-6x-faster-97f0803fa933) — WER + speed comparison. MEDIUM confidence (single source, ~2 years old).
- [adriszmar/whisper-large-v3-turbo-es (HuggingFace)](https://huggingface.co/adriszmar/whisper-large-v3-turbo-es) — Spanish fine-tune at 5.34% WER. MEDIUM confidence (community model, not used directly — informs the "large-v3 family is good for Spanish" claim).
- [Faster Whisper in Transana 5.30: Accuracy and Processing Speed (2025-05)](https://www.transana.com/blog/2025/05/01/faster-whisper-in-transana-5-30-accuracy-and-processing-speed-3-of-3/) — independent WER + speed comparison. MEDIUM confidence.
- [APScheduler PyPI](https://pypi.org/project/APScheduler/) — verified deployment model (in-process). HIGH confidence on rejection rationale.
- [Schedule jobs with systemd timers, a cron alternative (DEV)](https://dev.to/bowmanjd/schedule-jobs-with-systemd-timers-a-cron-alternative-15l8) — supports systemd-timer choice. MEDIUM confidence.
- [Linux Task Scheduling: cron vs systemd (mikihands, 2025-12)](https://blog.mikihands.com/en/whitedec/2025/12/12/linux-scheduling-cron-vs-systemd-timer/) — supports recommendation. MEDIUM confidence.
- In-repo verification: `pipeline/requirements.txt` (no `python-telegram-bot` pin present today), `pipeline/telegram.py` (curl-wrapping pattern), `extract_transcription.sh:28` (current `WHISPER_MODEL=medium` default), `~/.config/systemd/user/painforwisdom-daily-brief.{service,timer}` (precedent for systemd-user-timer scheduling). HIGH confidence — direct file reads.

---
*Stack research for: painforwisdom voicenote module*
*Researched: 2026-05-18*
