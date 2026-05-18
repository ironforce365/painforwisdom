# Technology Stack

**Analysis Date:** 2026-05-18

## Languages

**Primary:**
- Python 3.11 — pipeline orchestration, LangGraph nodes, Notion/WordPress/YouTube clients, NotebookLM publisher, daily summarizer (`pipeline/`, `scripts/`)
- Bash — Whisper wrapper, Telegram I/O wrapper, watchdog scripts (`extract_transcription.sh`, `telegram_io.sh`, `pipeline/scripts/check_daily_brief_freshness.sh`, `tests/sandbox_reset.sh`, `tests/smoke_pipeline.sh`)

**Secondary:**
- Markdown — Obsidian vault content + agent prompts (`obsidian-vault/gonzalo-book/`, `.claude/agents/`)
- SQL (SQLite) — LangGraph checkpoint store, themes DB (`pipeline/checkpoints.db`, `pipeline/state/themes.db`)

## Runtime

**Environment:**
- Python 3.11 via Conda env `painforwisdom-poc` (per `README.md` line 27)
- Linux host (systemd user units for scheduling — `painforwisdom-daily-brief.{service,timer}`)
- External binaries on PATH: `ffmpeg`, `ffprobe`, `whisper` (conda env `painforwisdom`), `nlm` (NotebookLM CLI), `claude` (Anthropic CLI for OAuth token), optional `gh` for PR ops

**Package Manager:**
- pip (`pipeline/requirements.txt`)
- Lockfile: none; `requirements.txt` uses pinned ranges (`>=X,<Y`) rather than a frozen lock

## Frameworks

**Core orchestration:**
- LangGraph `>=0.6,<0.7` — DAG, `StateGraph`, `RetryPolicy`, `interrupt()`/HITL, `Command` resume (`pipeline/graph.py`, `pipeline/run.py`)
- langgraph-checkpoint-sqlite `>=2.0,<3` — `SqliteSaver` durable checkpointer at `pipeline/checkpoints.db` (or sandbox via `CHECKPOINT_DB_PATH`)

**LLM:**
- litellm `>=1.55.0,<2` — Anthropic completion adapter, `litellm.token_counter`, `litellm.completion_cost` (`pipeline/llm.py`, `pipeline/cost_forecast.py`)
- anthropic `>=0.40.0,<1` — present for type compat; LiteLLM uses env-var auth path (1.83+) directly, no Anthropic SDK client construction

**Notion:**
- notion-client `>=2.2.1,<3` — official SDK; uses modern `data_sources.query` / `pages.create` API surface (`pipeline/notion_client.py`)

**HTTP:**
- httpx `>=0.27,<1` — WordPress REST client, daily-summarizer fetcher, research-node URL HEAD checks (`pipeline/wordpress_client.py`, `pipeline/summarize_daily/fetcher.py`, `pipeline/nodes/research.py`)

**Content extraction / media:**
- trafilatura `>=1.12,<2` — paywall heuristic + readable-text extraction for research URL verification (`pipeline/nodes/research.py`)
- markdown `>=3.5,<4` — markdown → HTML for WordPress posts (`pipeline/wordpress_client.py:markdown_to_html`)
- opencv-python-headless `>=4.8,<5` + numpy `>=1.26,<3` — smart frame selection (Laplacian variance focus + brightness) for blog featured images (`pipeline/image_extractor.py`)

**Google / YouTube:**
- google-api-python-client `>=2.110,<3`
- google-auth `>=2.25,<3`
- google-auth-oauthlib `>=1.2,<2`
- YouTube Data API v3 client with long-lived refresh-token flow (`pipeline/youtube_client.py`, `scripts/youtube_oauth_setup.py`)

**Config / serialization:**
- python-dotenv `>=1.0.0,<2` — profile-aware env loading (`.env` vs `.env.sandbox`) in `pipeline/run.py`
- PyYAML `>=6.0,<7` — config parsing
- Standard lib: `csv`, `json`, `sqlite3`, `subprocess`, `argparse`, `dataclasses`, `pathlib`, `re`

**Testing:**
- Plain pytest-style files in `tests/` (test_*.py) and shell smoke harness `tests/smoke_pipeline.sh` + `tests/sandbox_reset.sh`. No `pytest` pin in requirements — assumed system pytest.

**Transcription (external binaries, not pip):**
- OpenAI Whisper local CLI (Conda binary at `${HOME}/miniconda3/envs/painforwisdom/bin/whisper`) — default backend, model `medium` on auto device, CPU fallback on CUDA OOM (`extract_transcription.sh`)
- OpenAI Whisper API helper (Node-based, `~/.npm-global/lib/node_modules/openclaw/skills/openai-whisper-api/scripts/transcribe.sh`) — opt-in fallback via `WHISPER_BACKEND=openai|auto`

## Key Dependencies

**Critical:**
- langgraph `>=0.6,<0.7` — pipeline DAG topology + retries + HITL (`pipeline/graph.py`)
- litellm `>=1.55.0,<2` — Anthropic Sonnet 4.6 calls with web_search tool, OAuth subscription + API key fallback (`pipeline/llm.py`)
- notion-client `>=2.2.1,<3` — blog DB + research DB read/write (`pipeline/notion_client.py`)
- httpx `>=0.27,<1` — WordPress, daily-summarizer fetch, research URL verification

**Infrastructure:**
- langgraph-checkpoint-sqlite — durable resume for HITL `interrupt()`
- python-dotenv — split prod / sandbox profiles
- opencv-python-headless + numpy — featured-image frame scoring (avoids Qt GUI deps)
- google-api-python-client + google-auth + google-auth-oauthlib — YouTube uploads (lazy-imported in `pipeline/youtube_client.py`)

## Configuration

**Environment files:**
- `.env` — production profile (TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, OPENAI_API_KEY, NOTION_API_KEY, ANTHROPIC_AUTH_TOKEN or ANTHROPIC_API_KEY, optional NOTION_*_DATA_SOURCE_ID overrides, optional WORDPRESS_*, optional YOUTUBE_*, optional TELEGRAM_DAILY_SUMMARY_CHAT_ID)
- `.env.sandbox` — sandbox profile (duplicated Notion DBs, sandbox vault path, `[SANDBOX] ` Telegram prefix). Auto-loaded when `--profile sandbox` is passed
- `.env.sandbox.template` — committed template documenting required keys
- `~/.claude/.credentials.json` — read by `pipeline/token_rotation.py` on every LLM call; auto-rotated by `claude` CLI

**Build / runtime config:**
- `pipeline/requirements.txt` — pip dependency list
- `pipeline/.paperclip.json` — legacy Paperclip orchestrator IDs (no longer the runtime)
- `pipeline/config/youtube_metadata.json` — channel-level metadata defaults
- `pipeline/config/fetch_denylist.txt` — banned source domains for research fetcher
- `pipeline/state/theme_stats.json`, `pipeline/state/themes.db`, `pipeline/state/covered_angles/` — pipeline-local state for theme normalization + dedup
- `~/.config/systemd/user/painforwisdom-daily-brief.{service,timer}` — scheduled daily-brief unit (06:00 local)
- `pipeline/forecast.md` — token/cost forecast snapshot

**No package manifest:**
- No `pyproject.toml`, no `setup.py`, no `Makefile`. The pipeline is a runnable Python package invoked via `python -m pipeline.run` / `python -m pipeline.summarize_daily` / `python -m pipeline.cost_forecast` / `python -m pipeline.backfill_wordpress`.

**Git submodule:**
- `obsidian-vault/` → `https://github.com/gonandrap/painforwisdom-kb.git` branch `draft` (per `.gitmodules`). Pipeline writes commits directly to this submodule.

## Platform Requirements

**Development:**
- Linux (systemd user units)
- Python 3.11 conda env
- `ffmpeg` + `ffprobe` on PATH
- Local Whisper conda env at `${HOME}/miniconda3/envs/painforwisdom/`
- CUDA-capable GPU for fast Whisper (CPU fallback works, ~10× slower)
- `claude` CLI for subscription billing path
- `nlm` CLI (NotebookLM-CLI) for daily-summarizer NotebookLM publishing
- Optional: local zlibrary-mcp checkout for `pipeline/zlibrary_bridge.py` (set `ZLIBRARY_REPO_PATH`)

**Production:**
- Same host runs prod (no separate deploy target). The pipeline is single-user, single-machine. systemd user units provide scheduling and watchdog isolation.

## Entry Points

**Main pipeline:**
- `python -m pipeline.run --video <path>` / `--dir <path>` / `--from-transcript <path>` (`pipeline/run.py`)

**Daily summarizer:**
- `python -m pipeline.summarize_daily --apply --mcp-publish --count 3` (`pipeline/summarize_daily/__main__.py`)

**Cost forecast (pre-flight):**
- `python -m pipeline.cost_forecast --transcript <path>` / `--video <path>` (`pipeline/cost_forecast.py`)

**WordPress backfill:**
- `python -m pipeline.backfill_wordpress --profile prod --limit 1` (`pipeline/backfill_wordpress.py`)

**Smoke / sandbox:**
- `bash tests/sandbox_reset.sh && bash tests/smoke_pipeline.sh`

**One-shot setup helpers:**
- `python scripts/youtube_oauth_setup.py` — capture YouTube refresh token
- `python pipeline/smoke_notion.py` — Notion sanity check
- `python -m pipeline.scripts.migrate_notion_blog_schema --apply` — F6 Notion DB schema migration
- `python -m pipeline.scripts.migrate_notion_schema` — research DB schema migration
- `python -m pipeline.scripts.seed_themes_db` — seed `pipeline/state/themes.db`

**Shell entry points:**
- `./extract_transcription.sh <video> [lang] [date]` — Whisper transcription with confidence gating + auto quarantine
- `./telegram_io.sh send|wait_reply|ask <message>` — Telegram bot wrapper used by Python `pipeline/telegram.py`
- `pipeline/scripts/check_daily_brief_freshness.sh` — cron-friendly watchdog for the daily-brief timer

---

*Stack analysis: 2026-05-18*
