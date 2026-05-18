<!-- GSD:project-start source:PROJECT.md -->
## Project

**Voicenote — Long-Form Voice Capture for Book Vault**

A new capture surface in the `painforwisdom` repo that turns long-form Spanish voice notes (sent via Telegram, plus a one-shot backfill from Voicepal's Notion archive) into atomic English coaching-thought entries in the existing `obsidian-vault/gonzalo-book/` vault. Each long note typically contains 2–4 distinct thoughts; the new module splits, translates, and hands each chunk to the existing `coaching-thought-extractor` + `kb-curator` agents — so new entries flow into the same themes and book outline as today's video-derived entries.

This is purely Gonzalo's. No multi-user, no SaaS, no public bot.

**Core Value:** **More book-grade coaching thoughts captured per week, without paying Voicepal and without losing nuance from long Spanish voice notes.**

If everything else fails, this must work: a Spanish voice message sent via Telegram lands as one or more atomic English entries in the vault, reviewed before commit, slotted under the right themes, and traceable back to the source audio.

### Constraints

- **Tech stack:** Python 3.x, plain Python pipeline (NOT LangGraph for the voicenote flow — linear, doesn't need DAG/checkpoints), `python-telegram-bot==22.7` (one explicit new dep — verified NOT already in repo; current Telegram path is curl-via-`telegram_io.sh` which is insufficient for inline keyboards + getFile + CallbackQuery), stdlib `unittest`, `notion-client`, local Whisper (bumped to `large-v3` for voicenote runs). **Minimal new deps with explicit rationale; PTB is the only addition.**
- **Repo:** Single repo (`painforwisdom`). New module is `voicenote/`, sibling to `pipeline/`. No standalone service, no second deployment target.
- **Auth:** Gonzalo only. Telegram `user_id` allowlist. All other senders silently rejected. (Closes the `_wait_reply` chat-id gap noted in `CONCERNS.md`.)
- **Vendor cost:** No paid transcription vendor. Local Whisper only for v1. Translation uses the Anthropic LLM already wired in.
- **Latency:** Not real-time. Cron poll interval (likely 5–15 min) is acceptable.
- **Storage:** Raw `.ogg` retained per entry but `.gitignored`. Vault entries committed through the `obsidian-vault` submodule.
- **Privacy:** Notes and audio are personal. Nothing leaves the local host except (a) LLM API calls, (b) Telegram getUpdates polls, (c) Notion reads, (d) Notion blog writes already handled by `pipeline/`.
- **Quota:** User has Anthropic Ultra/Max subscription — token budget is generous, but prompts must still be bounded (cf. recent error-recovery prompt-growth fix). No silent feature drops if a planned LLM choice can't fit.
<!-- GSD:project-end -->

<!-- GSD:stack-start source:codebase/STACK.md -->
## Technology Stack

## Languages
- Python 3.11 — pipeline orchestration, LangGraph nodes, Notion/WordPress/YouTube clients, NotebookLM publisher, daily summarizer (`pipeline/`, `scripts/`)
- Bash — Whisper wrapper, Telegram I/O wrapper, watchdog scripts (`extract_transcription.sh`, `telegram_io.sh`, `pipeline/scripts/check_daily_brief_freshness.sh`, `tests/sandbox_reset.sh`, `tests/smoke_pipeline.sh`)
- Markdown — Obsidian vault content + agent prompts (`obsidian-vault/gonzalo-book/`, `.claude/agents/`)
- SQL (SQLite) — LangGraph checkpoint store, themes DB (`pipeline/checkpoints.db`, `pipeline/state/themes.db`)
## Runtime
- Python 3.11 via Conda env `painforwisdom-poc` (per `README.md` line 27)
- Linux host (systemd user units for scheduling — `painforwisdom-daily-brief.{service,timer}`)
- External binaries on PATH: `ffmpeg`, `ffprobe`, `whisper` (conda env `painforwisdom`), `nlm` (NotebookLM CLI), `claude` (Anthropic CLI for OAuth token), optional `gh` for PR ops
- pip (`pipeline/requirements.txt`)
- Lockfile: none; `requirements.txt` uses pinned ranges (`>=X,<Y`) rather than a frozen lock
## Frameworks
- LangGraph `>=0.6,<0.7` — DAG, `StateGraph`, `RetryPolicy`, `interrupt()`/HITL, `Command` resume (`pipeline/graph.py`, `pipeline/run.py`)
- langgraph-checkpoint-sqlite `>=2.0,<3` — `SqliteSaver` durable checkpointer at `pipeline/checkpoints.db` (or sandbox via `CHECKPOINT_DB_PATH`)
- litellm `>=1.55.0,<2` — Anthropic completion adapter, `litellm.token_counter`, `litellm.completion_cost` (`pipeline/llm.py`, `pipeline/cost_forecast.py`)
- anthropic `>=0.40.0,<1` — present for type compat; LiteLLM uses env-var auth path (1.83+) directly, no Anthropic SDK client construction
- notion-client `>=2.2.1,<3` — official SDK; uses modern `data_sources.query` / `pages.create` API surface (`pipeline/notion_client.py`)
- httpx `>=0.27,<1` — WordPress REST client, daily-summarizer fetcher, research-node URL HEAD checks (`pipeline/wordpress_client.py`, `pipeline/summarize_daily/fetcher.py`, `pipeline/nodes/research.py`)
- trafilatura `>=1.12,<2` — paywall heuristic + readable-text extraction for research URL verification (`pipeline/nodes/research.py`)
- markdown `>=3.5,<4` — markdown → HTML for WordPress posts (`pipeline/wordpress_client.py:markdown_to_html`)
- opencv-python-headless `>=4.8,<5` + numpy `>=1.26,<3` — smart frame selection (Laplacian variance focus + brightness) for blog featured images (`pipeline/image_extractor.py`)
- google-api-python-client `>=2.110,<3`
- google-auth `>=2.25,<3`
- google-auth-oauthlib `>=1.2,<2`
- YouTube Data API v3 client with long-lived refresh-token flow (`pipeline/youtube_client.py`, `scripts/youtube_oauth_setup.py`)
- python-dotenv `>=1.0.0,<2` — profile-aware env loading (`.env` vs `.env.sandbox`) in `pipeline/run.py`
- PyYAML `>=6.0,<7` — config parsing
- Standard lib: `csv`, `json`, `sqlite3`, `subprocess`, `argparse`, `dataclasses`, `pathlib`, `re`
- Plain pytest-style files in `tests/` (test_*.py) and shell smoke harness `tests/smoke_pipeline.sh` + `tests/sandbox_reset.sh`. No `pytest` pin in requirements — assumed system pytest.
- OpenAI Whisper local CLI (Conda binary at `${HOME}/miniconda3/envs/painforwisdom/bin/whisper`) — default backend, model `medium` on auto device, CPU fallback on CUDA OOM (`extract_transcription.sh`)
- OpenAI Whisper API helper (Node-based, `~/.npm-global/lib/node_modules/openclaw/skills/openai-whisper-api/scripts/transcribe.sh`) — opt-in fallback via `WHISPER_BACKEND=openai|auto`
## Key Dependencies
- langgraph `>=0.6,<0.7` — pipeline DAG topology + retries + HITL (`pipeline/graph.py`)
- litellm `>=1.55.0,<2` — Anthropic Sonnet 4.6 calls with web_search tool, OAuth subscription + API key fallback (`pipeline/llm.py`)
- notion-client `>=2.2.1,<3` — blog DB + research DB read/write (`pipeline/notion_client.py`)
- httpx `>=0.27,<1` — WordPress, daily-summarizer fetch, research URL verification
- langgraph-checkpoint-sqlite — durable resume for HITL `interrupt()`
- python-dotenv — split prod / sandbox profiles
- opencv-python-headless + numpy — featured-image frame scoring (avoids Qt GUI deps)
- google-api-python-client + google-auth + google-auth-oauthlib — YouTube uploads (lazy-imported in `pipeline/youtube_client.py`)
## Configuration
- `.env` — production profile (TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, OPENAI_API_KEY, NOTION_API_KEY, ANTHROPIC_AUTH_TOKEN or ANTHROPIC_API_KEY, optional NOTION_*_DATA_SOURCE_ID overrides, optional WORDPRESS_*, optional YOUTUBE_*, optional TELEGRAM_DAILY_SUMMARY_CHAT_ID)
- `.env.sandbox` — sandbox profile (duplicated Notion DBs, sandbox vault path, `[SANDBOX] ` Telegram prefix). Auto-loaded when `--profile sandbox` is passed
- `.env.sandbox.template` — committed template documenting required keys
- `~/.claude/.credentials.json` — read by `pipeline/token_rotation.py` on every LLM call; auto-rotated by `claude` CLI
- `pipeline/requirements.txt` — pip dependency list
- `pipeline/.paperclip.json` — legacy Paperclip orchestrator IDs (no longer the runtime)
- `pipeline/config/youtube_metadata.json` — channel-level metadata defaults
- `pipeline/config/fetch_denylist.txt` — banned source domains for research fetcher
- `pipeline/state/theme_stats.json`, `pipeline/state/themes.db`, `pipeline/state/covered_angles/` — pipeline-local state for theme normalization + dedup
- `~/.config/systemd/user/painforwisdom-daily-brief.{service,timer}` — scheduled daily-brief unit (06:00 local)
- `pipeline/forecast.md` — token/cost forecast snapshot
- No `pyproject.toml`, no `setup.py`, no `Makefile`. The pipeline is a runnable Python package invoked via `python -m pipeline.run` / `python -m pipeline.summarize_daily` / `python -m pipeline.cost_forecast` / `python -m pipeline.backfill_wordpress`.
- `obsidian-vault/` → `https://github.com/gonandrap/painforwisdom-kb.git` branch `draft` (per `.gitmodules`). Pipeline writes commits directly to this submodule.
## Platform Requirements
- Linux (systemd user units)
- Python 3.11 conda env
- `ffmpeg` + `ffprobe` on PATH
- Local Whisper conda env at `${HOME}/miniconda3/envs/painforwisdom/`
- CUDA-capable GPU for fast Whisper (CPU fallback works, ~10× slower)
- `claude` CLI for subscription billing path
- `nlm` CLI (NotebookLM-CLI) for daily-summarizer NotebookLM publishing
- Optional: local zlibrary-mcp checkout for `pipeline/zlibrary_bridge.py` (set `ZLIBRARY_REPO_PATH`)
- Same host runs prod (no separate deploy target). The pipeline is single-user, single-machine. systemd user units provide scheduling and watchdog isolation.
## Entry Points
- `python -m pipeline.run --video <path>` / `--dir <path>` / `--from-transcript <path>` (`pipeline/run.py`)
- `python -m pipeline.summarize_daily --apply --mcp-publish --count 3` (`pipeline/summarize_daily/__main__.py`)
- `python -m pipeline.cost_forecast --transcript <path>` / `--video <path>` (`pipeline/cost_forecast.py`)
- `python -m pipeline.backfill_wordpress --profile prod --limit 1` (`pipeline/backfill_wordpress.py`)
- `bash tests/sandbox_reset.sh && bash tests/smoke_pipeline.sh`
- `python scripts/youtube_oauth_setup.py` — capture YouTube refresh token
- `python pipeline/smoke_notion.py` — Notion sanity check
- `python -m pipeline.scripts.migrate_notion_blog_schema --apply` — F6 Notion DB schema migration
- `python -m pipeline.scripts.migrate_notion_schema` — research DB schema migration
- `python -m pipeline.scripts.seed_themes_db` — seed `pipeline/state/themes.db`
- `./extract_transcription.sh <video> [lang] [date]` — Whisper transcription with confidence gating + auto quarantine
- `./telegram_io.sh send|wait_reply|ask <message>` — Telegram bot wrapper used by Python `pipeline/telegram.py`
- `pipeline/scripts/check_daily_brief_freshness.sh` — cron-friendly watchdog for the daily-brief timer
<!-- GSD:stack-end -->

<!-- GSD:conventions-start source:CONVENTIONS.md -->
## Conventions

## Naming Patterns
- `snake_case.py` throughout. Example: `pipeline/nodes/kb_curator.py`,
- One concern per module. `pipeline/runtime.py` comment is explicit:
- Module file: `pipeline/nodes/<stage>.py` (e.g. `extract.py`,
- Entry-point callable: `node_<stage>(state: State) -> Dict[str, Any]`.
- Stage label used in logging / metrics: bare `<stage>` (no `node_`
- Located in `.claude/agents/<agent-name>.md`, kebab-case.
- Filenames map 1:1 to node callsites via
- Active agents: `coaching-thought-extractor.md`, `kb-curator.md`,
- Theme / framework slugs: `kebab-case-lowercase`. Examples:
- Vault entry filenames: `YYYY-MM-DD-2-to-4-word-kebab.md`. Example:
- `slugify()` in `pipeline/runtime.py:110` enforces this for
- `snake_case` for functions and locals. Module-private helpers
- Module-level constants: `UPPER_SNAKE_CASE` (e.g. `PROJECT_ROOT`,
- `TypedDict` field names: `snake_case` (matches LangGraph state
## Code Style
- 4-space indent.
- Soft wrap ~95 cols; docstrings and prose wrap ~80.
- Trailing comma on multi-line tuples / lists / kwargs.
- No `__all__` declarations. Imports are filtered by leading `_`
- `TypedDict` with `total=False` for LangGraph state
- `dataclasses` for small value types (e.g. `LocalBook` in
- Plain `Dict[str, Any]` for LLM payloads and Notion responses.
## Import Organization
## Error Handling
## Logging
| Prefix | Source |
|--------|--------|
| `[transcribe]` | `pipeline/nodes/transcribe.py` |
| `[extract]` | `pipeline/nodes/extract.py` |
| `[kb-curator]` | `pipeline/nodes/kb_curator.py` |
| `[writer]` | `pipeline/nodes/writer.py` |
| `[research]` | `pipeline/nodes/research.py` |
| `[validator]` | `pipeline/nodes/validator.py` |
| `[run]` | `pipeline/run.py` |
| `[batch]` | `pipeline/run.py` |
| `[retry]` | `pipeline/retry.py` |
| `[smoke]` | `tests/smoke_pipeline.sh` |
- stdout for live runs,
- `runs.jsonl` per run-dir for telemetry,
- `journalctl --user -u painforwisdom-daily-brief.service` for
## Function & Module Design
## Prompt Storage Conventions
## Environment Variable Conventions
| Var | Purpose | Default behavior |
|-----|---------|------------------|
| `ANTHROPIC_AUTH_TOKEN` | Subscription Bearer token. Auto-refreshed from `~/.claude/.credentials.json` on every call (`pipeline/llm.py:_refresh_auth`). | Required for subscription path. |
| `ANTHROPIC_API_KEY` | Pay-per-token fallback (`x-api-key`). | Used only when no subscription token. |
| `PIPELINE_MODEL` | Override default model. | Default `claude-sonnet-4-6` (see `pipeline/nodes/extract.py:97`). |
| `VAULT_PATH` | Obsidian vault root. | Default `<project>/obsidian-vault`. Sandbox sets `<project>/obsidian-vault-sandbox`. |
| `CHECKPOINT_DB_PATH` | LangGraph SqliteSaver path. | Default `pipeline/checkpoints.db`. Sandbox uses `pipeline/checkpoints-sandbox.db`. |
| `NOTION_API_KEY` | Notion integration token. | Required for `notion_blog`, `notion_research`. |
| `NOTION_BLOG_DATA_SOURCE_ID` | UUID of blog DB data source. | **Data source UUID, not database name** (see OPERATIONS.md §6). |
| `NOTION_RESEARCH_DATA_SOURCE_ID` | UUID of research DB data source. | Same. |
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` | Main pipeline chat. | Required. |
| `TELEGRAM_DAILY_SUMMARY_CHAT_ID` | Daily brief channel. | Falls back to `TELEGRAM_CHAT_ID`. |
| `TELEGRAM_MESSAGE_PREFIX` | Prepended to every send. | Set to `[SANDBOX] ` by `pipeline/run.py:69` under `--profile sandbox`. |
| `TELEGRAM_PARSE_MODE` | Telegram parse mode for the next send. | Unset = plain text. Used by daily brief for HTML `<a href>` links. |
| `WORDPRESS_ENABLED`, `WORDPRESS_SITE`, `WORDPRESS_USERNAME`, `WORDPRESS_APP_PASSWORD`, `WORDPRESS_AUTH_MODE`, `WORDPRESS_OAUTH_TOKEN` | WordPress draft node. | Skips gracefully if disabled or missing creds (writes dormant bundle). |
| `YOUTUBE_ENABLED`, `YOUTUBE_CLIENT_ID`, `YOUTUBE_CLIENT_SECRET`, `YOUTUBE_REFRESH_TOKEN` | YouTube upload node. | Skips on missing creds. |
| `ZLIBRARY_EMAIL`, `ZLIBRARY_PASSWORD`, `ZLIBRARY_DOWNLOAD_DIR`, `ZLIBRARY_REPO_PATH`, `ZLIBRARY_TIMEOUT_S` | Research book download bridge. | Used by `pipeline/zlibrary_bridge.py`. |
| `PAINFORWISDOM_THEMES_DB` | Path override for the themes SQLite DB. | Default `pipeline/state/themes.db`. Used by tests via `_TempDB` (`tests/test_themes_db.py:20`). |
| `PAINFORWISDOM_THEMES_YAML` | Path override for the themes seed YAML. | Used by `pipeline/scripts/seed_themes_db.py`. |
| `NLM_PROFILE`, `LISTENER_NAME` | NotebookLM CLI integration. | Used by `pipeline/summarize_daily/notebooklm_publisher.py`. |
## Commit Message Style
- **First word identifies a scope** (`LLM:`, `Telegram:`, `Watchdog:`,
- Sentence case after the scope. No trailing period.
- Multi-concern commits are explicitly joined with `;` so reviewers
- `chore:` is the only Conventional-Commit prefix that appears, and
- Subject ends with ` (#N)` — the PR number is appended on merge.
- No `Co-Authored-By` line in current main; commits are direct from
## Vault Content Conventions
## Core Insight
## Story Anchor
## Framework Connection
## Practical Application
## Who It's For
## Integrity Check
## Blog Post Seed
## Raw Transcript Notes
- ...
## Research
## Core tension
## Key insight so far
## Entries
| Date | Entry | Core Insight |
|------|-------|--------------|
| YYYY-MM-DD | [[YYYY-MM-DD-slug]] | one-sentence insight |
## Patterns emerging
## Possible chapter angle
## Definition
## Entries referencing this
- [[YYYY-MM-DD-slug]] — connection narrative ...
## Evolution
- YYYY-MM-DD: ...
## OPERATIONS.md Runbook Rules
- **Conda env activation first.** Every command assumes
- **`crontab -l` is empty by design** (`OPERATIONS.md:189`). All
- **Daily-brief retry budget = 3 starts in 2h.**
- **`--auto-approve` is TEST ONLY.** `pipeline/run.py:461` and
- **`--telegram-on-error`** pairs with `--auto-approve` for the smoke
- **`pipeline.cost_forecast`** is the cost preview command. Memory
- **Run-ID collision protection** — batch mode appends `_NNN` to every
- **Resume-after-HITL.** Re-launching with the same `--run-id` resumes
## Quick Reference — Where Things Live
| Concern | Path |
|---------|------|
| Pipeline entry point | `pipeline/run.py` |
| DAG topology + RetryPolicy | `pipeline/graph.py` |
| Per-stage logic | `pipeline/nodes/<stage>.py` |
| Shared state schema | `pipeline/state.py` |
| Per-node input contracts | `pipeline/contracts.py` |
| LLM wrapper + auth refresh | `pipeline/llm.py` |
| Prompt loader + cache padding | `pipeline/runtime.py:load_agent_prompt` |
| Agent prompts (stripped to body) | `.claude/agents/*.md` |
| Telegram I/O | `pipeline/telegram.py` + `telegram_io.sh` |
| Notion REST helpers | `pipeline/notion_client.py` |
| WordPress client + dormant bundle | `pipeline/wordpress_client.py` |
| YouTube client | `pipeline/youtube_client.py` |
| Smart-frame image extraction | `pipeline/image_extractor.py` |
| Cost forecasting (pre-flight) | `pipeline/cost_forecast.py` |
| Notion smoke test | `pipeline/smoke_notion.py` |
| Daily-summary subsystem | `pipeline/summarize_daily/` |
| Themes SQLite + seed | `pipeline/themes_db.py`, `pipeline/scripts/seed_themes_db.py` |
| Banned-source list | `pipeline/banned_sources.py` |
| Tests (stdlib unittest) | `tests/*.py` |
| Smoke harness | `tests/smoke_pipeline.sh` |
| Sandbox reset | `tests/sandbox_reset.sh` |
| Transcript fixtures | `tests/fixtures/transcript_*.txt` |
| Vault content | `obsidian-vault/gonzalo-book/` |
| Per-run artifacts | `processed/<run_id>/<suffix>/` (gitignored) |
| Day-to-day commands | `OPERATIONS.md` |
| High-level overview | `README.md` |
<!-- GSD:conventions-end -->

<!-- GSD:architecture-start source:ARCHITECTURE.md -->
## Architecture

## Pattern Overview
- One TypedDict `State` shared by all nodes; partial-update merge semantics via LangGraph reducers (`pipeline/state.py`).
- Fan-out / fan-in topology rooted at `extract`, with three independent downstream branches that re-converge at `wordpress_draft` and `validator`.
- All file I/O is owned by pipeline nodes — LLM agent prompts under `.claude/agents/` are loaded as system prompts but the file-writing instructions in those prompts are stripped (`pipeline/runtime.py:load_agent_prompt`); the model returns inline structured text and the node writes to disk.
- External side effects are isolated per node: `notion_client.py` (REST), `extract_transcription.sh` (Whisper CLI), `wordpress_client.py` (REST), `youtube_client.py` (Google API), `telegram_io.sh` (curl). LLM access is funneled through `pipeline/llm.py` (LiteLLM + OAuth subscription auth).
- Two distinct top-level pipelines: per-video content pipeline (`pipeline.run`) and the daily research-brief pipeline (`pipeline.summarize_daily`). They share the `State`/telemetry conventions but have separate graphs and CLIs.
## Layers
- Purpose: CLI entry, profile selection, batch driving, HITL/error round-trips.
- Location: `pipeline/run.py`, `pipeline/retry.py`
- Contains: argparse, env-profile dispatch (`prod` vs `sandbox`), per-video looping, Telegram reply loops, video archive/quarantine moves, run-id minting.
- Depends on: `pipeline.graph`, `pipeline.telegram`, `pipeline.contracts`.
- Used by: human operators, systemd timers, `/retry-failed` Claude skill.
- Purpose: Declares the DAG topology, retry policy, checkpointer.
- Location: `pipeline/graph.py`
- Contains: `build_graph(start_at)` factory, `_is_transient` classifier, `RetryPolicy` (3 attempts, exponential 2s→60s, jitter), `SqliteSaver` wiring to `pipeline/checkpoints.db` (or `checkpoints-sandbox.db`).
- Depends on: every `pipeline/nodes/*.py`, `pipeline.state`.
- Used by: `pipeline.run`, `pipeline.retry`.
- Purpose: One file per stage; each is a pure function `State -> Dict[str, Any]` that updates state.
- Location: `pipeline/nodes/*.py`
- Contains: stage I/O, prompt loading, parsing, on-disk artifact writes under `processed/<run_id>/<run_suffix>/<stage>/`.
- Depends on: `pipeline.contracts.assert_inputs`, `pipeline.llm.call_llm`, `pipeline.runtime`, side-effect clients (`pipeline.notion_client`, `pipeline.wordpress_client`, `pipeline.youtube_client`, `pipeline.image_extractor`).
- Used by: `pipeline.graph`.
- Purpose: Thin wrappers around external systems. No business logic, no orchestration.
- Files:
- `pipeline/state.py` — TypedDict for the shared state object.
- `pipeline/contracts.py` — per-node input contract assertions; raise `InputContractError` (treated as non-retryable).
- `pipeline/runtime.py` — `load_agent_prompt`, `append_metric`, `date_from_filename`, `slugify`, `parse_extraction_field`, `VAULT_PATH` resolution from env, `CACHE_PADDING_APPENDIX` (EXECUTION CONTEXT block appended to every system prompt to clear the Sonnet 4.6 cache-engagement floor of ~2200 tokens).
- `pipeline/themes_db.py` — SQLite cache over `data/themes.yaml` (theme → research agent routing).
- Location: `pipeline/summarize_daily/`
- Files:
- Entry point: `python -m pipeline.summarize_daily --apply --mcp-publish --count 3`.
## Data Flow
### Per-video content pipeline (`python -m pipeline.run --video <mp4>`)
```
```
### Daily-brief pipeline (`python -m pipeline.summarize_daily`)
### State Management
```
```
## Key Abstractions
- Purpose: Single shared dict mutated by every node.
- Definition: `pipeline/state.py`
- Pattern: `total=False` so every key is optional; nodes return partial dicts which LangGraph merges.
- Purpose: Pure stage logic, single entry point per stage.
- Examples: `pipeline/nodes/extract.py:node_extract`, `pipeline/nodes/kb_curator.py:node_kb_curator`, …
- Pattern: signature `def node_<name>(state: State) -> Dict[str, Any]`; first line is always `assert_inputs("<name>", state)` per the contract registry in `pipeline/contracts.py:NODE_INPUTS`.
- Purpose: Conceptual carry-over from the legacy Paperclip orchestrator — each node still loads its system prompt from a markdown file under `.claude/agents/`.
- Examples: `.claude/agents/coaching-thought-extractor.md`, `.claude/agents/kb-curator.md`, `.claude/agents/painforwisdom-writer.md`, `.claude/agents/research-curator.md`, `.claude/agents/notion-blog-post-logger.md`, `.claude/agents/notion-research-logger.md`, `.claude/agents/youtube-upload-agent.md`, `.claude/agents/pipeline-summary.md`.
- Pattern: `pipeline/runtime.py:load_agent_prompt` strips the YAML frontmatter and the `## OUTPUT` section (those instructions assume Bash/Write tool access the LangGraph node does not give the model) and appends `CACHE_PADDING_APPENDIX` so the prompt clears the empirical Sonnet 4.6 cache floor of ~2200 tokens.
- Purpose: Single structured payload the LLM emits, parsed by the node to drive vault writes.
- Definition: YAML inside `---kb-plan---` markers. See `pipeline/nodes/kb_curator.py:_KB_OUTPUT_SPEC`.
- Pattern: One of three `action` values (`PROCEED`, `NEEDS_APPROVAL_THEME`, `NEEDS_APPROVAL_FRAMEWORK`). On approval-needed, the node calls `interrupt()` and the orchestrator handles the Telegram round-trip.
## Entry Points
- Location: `pipeline/run.py`
- Invocation: `python -m pipeline.run --video <mp4>` | `--dir <videos-dir>` | `--from-transcript <txt>`
- Profile selection: `--profile prod` (loads `.env`) or `--profile sandbox` (loads `.env.sandbox`, prefixes Telegram with `[SANDBOX] `, redirects to sandbox vault + checkpoint DB).
- Triggers: Manual operator runs, `/extract-transcription` skill (transcription only), `/retry-failed` skill (for `to_be_retried/`).
- Location: `pipeline/retry.py`
- Invocation: `python -m pipeline.retry [--run-id <id>] [--video <mp4>] [--quarantine bulk/quarantine]`
- Triggers: After a quarantined batch run. Default mode scans `bulk/quarantine/`, matches each video to its checkpoint thread, and resumes via `graph.invoke(None, config={"thread_id": run_id})`.
- Location: `pipeline/summarize_daily/__main__.py`
- Invocation: `python -m pipeline.summarize_daily {--dry-run|--apply} [--mcp-publish] [--count N]`
- Triggers: `painforwisdom-daily-brief.timer` systemd user unit at 06:00 local daily.
- Location: `pipeline/scripts/check_daily_brief_freshness.sh`
- Schedule: Cron (so it survives the same failure modes that would kill a systemd timer).
- Logic: Alerts via Telegram if newest `briefs/<theme>/<date>--<slug>/` mtime older than `MAX_AGE_HOURS=25h`, AND/OR if `systemctl --user is-active painforwisdom-daily-brief.timer` is not `active`. Heal-then-notify: attempts a `systemctl --user start` before alerting. Dedupes via `data/.daily_brief_watchdog.last_alert` (12h window). Alerts route to the dedicated `daily_summary` Telegram channel.
- `python -m pipeline.smoke_notion` — verify Notion API auth + DB schema.
- `python -m pipeline.cost_forecast --transcript <txt>` / `--video <mp4>` — token+$+quota forecast.
- `python -m pipeline.scripts.audit_research_tasks` — Notion research-row audit.
- `python -m pipeline.scripts.seed_themes_db` — apply `data/themes.yaml` to `pipeline/state/themes.db`.
- `python -m pipeline.scripts.render_curator_taxonomy --apply` — regenerate the taxonomy block in `.claude/agents/research-curator.md`.
- `bash extract_transcription.sh <video> <lang> <date>` — standalone Whisper helper (also used by Stage 1).
- `bash telegram_io.sh {send|ask|wait_reply} ...` — Telegram I/O primitive.
- `bash tests/smoke_pipeline.sh` / `bash tests/sandbox_reset.sh` — sandbox driver + reset.
## Error Handling
- **Transient classifier:** `pipeline/graph.py:_is_transient` returns True for `httpx.HTTPError`, `socket.timeout`, `ConnectionError`, `subprocess.TimeoutExpired`, and litellm/Notion 5xx-class exceptions. `InputContractError` and everything else is treated as persistent.
- **RetryPolicy:** 3 attempts, 2s → 60s exponential backoff with jitter, applied to `transcribe`, `extract`, `kb_curator`, `writer`, `research`, `notion_blog`, `notion_research`. The newer parallel nodes (`extract_image`, `youtube_upload`, `wordpress_draft`) intentionally carry no retry policy — they catch their own errors and degrade gracefully (set `*_skipped=True` or `image_extraction_failed=True`).
- **Non-recoverable classifier:** `pipeline/run.py:_classify_non_recoverable` surfaces specific error strings (Anthropic "Extra usage is required for long context requests", auth-credential rejection) and tells the user `retry will fail identically — reply abort`.
- **HITL approval loop:** kb_curator's `interrupt()` → `_get_pending_interrupt` → `_ask_indefinitely` (reposts the prompt every `--reminder-interval` seconds, defaults 30 min, waits forever). The orchestrator resumes the graph with `Command(resume=<reply text>)`.
- **Error-recovery loop:** Bounded — `_ask_bounded(prompt, reminder_interval, max_reminders=5)` so a stuck pipeline does not block forever waiting for a Telegram reply on an error prompt.
- **CUDA OOM fallback:** `pipeline/nodes/transcribe.py` detects `CUDA out of memory` in Whisper stderr and re-invokes with `WHISPER_DEVICE=cpu`. Notifies Telegram that wall-clock will balloon ~10×.
- **Token rotation:** `pipeline/llm.py:_refresh_auth` re-reads `~/.claude/.credentials.json` on every call (and again on 401). Lets the `claude` CLI rotate the OAuth token mid-run without breaking the pipeline.
## Cross-Cutting Concerns
- Per-stage JSONL appended to `<run_dir>/runs.jsonl` via `pipeline/runtime.py:append_metric`. One row per stage: duration, tokens (input/output/cache_read/cache_creation), billing mode, model, plus stage-specific fields.
- Daily-summarizer telemetry appended to `reports/daily-summarizer-runs.jsonl`.
- Watchdog status appended to `data/daily_brief_watchdog.log`.
- Stdout per-stage `[stage] start/done` lines for live tailing.
- Anthropic: OAuth subscription preferred (`ANTHROPIC_AUTH_TOKEN`, refreshed from `~/.claude/.credentials.json`); API-key fallback (`ANTHROPIC_API_KEY`). Subscription path requires `extra_headers={"anthropic-beta": "oauth-2025-04-20"}` and the `CLAUDE_CODE_IDENTITY` system block. Opt-in 1M-context tier (`context-1m-2025-08-07`) is per-call via the `long_context=True` kwarg to `call_llm`.
- Notion: `NOTION_API_KEY` (internal integration token, distinct from MCP/claude.ai auth). Data source UUIDs in env or hardcoded as prod fallbacks in `pipeline/notion_client.py`.
- Telegram: `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` (main channel) + `TELEGRAM_DAILY_SUMMARY_CHAT_ID` (daily-brief channel).
- WordPress: `WORDPRESS_*` creds; gated by `WORDPRESS_ENABLED=true` (dormant by default).
- YouTube: OAuth via `scripts/youtube_oauth_setup.py`; gated by `YOUTUBE_ENABLED=true`.
- `.env` vs `.env.sandbox` (loaded by `pipeline/run.py:_peek_profile` BEFORE pipeline imports — pipeline modules read env at import time).
- Sandbox redirects `VAULT_PATH` to `obsidian-vault-sandbox/` (a git worktree of the same vault repo), uses sandbox Notion data sources, and uses `pipeline/checkpoints-sandbox.db`.
- Sandbox messages get `[SANDBOX] ` prefix via `TELEGRAM_MESSAGE_PREFIX`.
- Per-node input contracts in `pipeline/contracts.py:NODE_INPUTS`. Missing required field → `InputContractError` → orchestrator escalates as non-recoverable.
- Final-stage audit in `pipeline/nodes/validator.py` — pure Python, no LLM, produces PASS/PARTIAL/FAIL verdict.
- `pipeline/telegram.py` is the only Python wrapper; everything else calls `send()` or `ask()`. It shells out to `telegram_io.sh` (curl-based, no Telegram SDK dependency).
- Per-call `chat_id` override (used by daily summarizer to route to `daily_summary` channel without polluting `content_pipeline`).
- Per-call `parse_mode` override (HTML for the daily brief link rendering; plain text for everything else).
- Touchpoints: run intake, HITL approval prompt (kb_curator), error escalation prompt (`_drive_graph`), validator summary, batch summary, retry summary, CUDA OOM alert, daily-brief audio-ready message, brief-crash alert, fetch-failure alert, watchdog alert.
- The vault under `obsidian-vault/gonzalo-book/` is the canonical content store. The pipeline writes into it and downstream sinks (Notion, WordPress, YouTube) are derived from it.
- Entries: `obsidian-vault/gonzalo-book/entries/YYYY-MM-DD-slug.md` — one per video, immutable after creation (kb_curator refuses overwrite).
- Themes: `obsidian-vault/gonzalo-book/themes/<slug>.md` — coarse buckets (~11 active themes per memory note "Vault vs Notion themes"); each has Core tension + Key insight + entry table. kb_curator rewrites the whole theme file on every entry that touches it.
- Frameworks: `obsidian-vault/gonzalo-book/frameworks/<slug>.md` — named conceptual models (e.g. `cookie-jar-types`, `friction-types`, `phase-1-protocol`).
- Timeline: `obsidian-vault/gonzalo-book/_index.md` — master timeline table; kb_curator appends one row per entry.
- Book outline: `obsidian-vault/gonzalo-book/book-outline.md` — auto-maintained synthesis; kb_curator rewrites the whole file as new patterns emerge across entries.
- Research index: `obsidian-vault/gonzalo-book/research-index.md` — verified references organized by topic.
- Submodule: pinned to the `draft` branch of `gonandrap/painforwisdom-kb` (see `.gitmodules`). Pipeline commits land on `draft`, manual merge to `main` after review.
<!-- GSD:architecture-end -->

<!-- GSD:skills-start source:skills/ -->
## Project Skills

| Skill | Description | Path |
|-------|-------------|------|
| extract-transcription | > Extract a transcription from a video file using Whisper via extract_transcription.sh. Usage: /extract-transcription <video_file> [language] [date YYYY-MM-DD] | `.claude/skills/extract-transcription/SKILL.md` |
| retry-failed | > Retry transcripts in to_be_retried/ through the full content pipeline. Usage: /retry-failed [filename] If no filename is given, lists all pending files and processes them all. | `.claude/skills/retry-failed/SKILL.md` |
<!-- GSD:skills-end -->

<!-- GSD:workflow-start source:GSD defaults -->
## GSD Workflow Enforcement

Before using Edit, Write, or other file-changing tools, start work through a GSD command so planning artifacts and execution context stay in sync.

Use these entry points:
- `/gsd-quick` for small fixes, doc updates, and ad-hoc tasks
- `/gsd-debug` for investigation and bug fixing
- `/gsd-execute-phase` for planned phase work

Do not make direct repo edits outside a GSD workflow unless the user explicitly asks to bypass it.
<!-- GSD:workflow-end -->



<!-- GSD:profile-start -->
## Developer Profile

> Profile not yet configured. Run `/gsd-profile-user` to generate your developer profile.
> This section is managed by `generate-claude-profile` -- do not edit manually.
<!-- GSD:profile-end -->
