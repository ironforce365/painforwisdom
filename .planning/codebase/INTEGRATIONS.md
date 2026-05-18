# External Integrations

**Analysis Date:** 2026-05-18

## APIs & External Services

**LLM (content generation):**
- Anthropic Claude (Sonnet 4.6 by default; model overridable via `PIPELINE_MODEL`) — every content stage (`extract`, `kb_curator`, `writer`, `research`, `youtube_upload` metadata, daily-summarizer brief writing)
  - SDK/Client: `litellm>=1.55.0,<2` (`pipeline/llm.py`). The `anthropic` SDK is in `requirements.txt` for type/feature compat but LiteLLM is the call path.
  - Two auth modes, decided per call:
    - Subscription (Pro/Max) — `ANTHROPIC_AUTH_TOKEN` Bearer, refreshed each call from `~/.claude/.credentials.json` via `pipeline/token_rotation.py`. Requires `extra_headers={"anthropic-beta": "oauth-2025-04-20"}` and a `CLAUDE_CODE_IDENTITY` system block prepended.
    - API credits — `ANTHROPIC_API_KEY` x-api-key. Used when no OAuth token is present.
  - Long-context opt-in: `context-1m-2025-08-07` beta header attached per-call when `long_context=True`. Bounded to avoid the "Extra usage required" rate-limit error on small requests.
  - Server-side web_search tool: `web_search_20250305` attached when `web_search=True` (research node only).

**Transcription:**
- OpenAI Whisper — Stage 1 transcription
  - Primary path: local conda binary at `${HOME}/miniconda3/envs/painforwisdom/bin/whisper`, model `medium` (overridable via `WHISPER_MODEL`), device auto / CPU fallback on CUDA OOM (`extract_transcription.sh`, `pipeline/nodes/transcribe.py`)
  - Fallback path: OpenAI Whisper REST API via helper script at `~/.npm-global/lib/node_modules/openclaw/skills/openai-whisper-api/scripts/transcribe.sh`, gated by `WHISPER_BACKEND=openai|auto`. Requires `OPENAI_API_KEY`.
  - Confidence gating: segments with `no_speech_prob>0.5`, `avg_logprob<-1.0`, or `compression_ratio>2.4` are flagged; ≥20% bad segments triggers auto-quarantine + Telegram alert (exit 2).

**Notion (knowledge ops):**
- Notion REST API via `notion-client>=2.2.1,<3` (`pipeline/notion_client.py`)
  - Auth: `NOTION_API_KEY` — Notion internal-integration token (different from the MCP/claude.ai connector token used historically). Both DBs must be shared with the integration in the Notion UI.
  - Pacing: hard-coded 0.4s sleep between requests (3 req/s limit).
  - Production databases (used by `pipeline/nodes/notion_blog.py`, `pipeline/nodes/notion_research.py`, `pipeline/backfill_wordpress.py`, `pipeline/summarize_daily/`):
    - **Blog post pending publications** — DB ID `3185901befa9800489d2dcd03fdb5ec8`, data-source ID `3185901b-efa9-8099-baac-000b2cb04d03` (overridable via `NOTION_BLOG_DATA_SOURCE_ID`)
    - **Research Tasks** — DB ID `64b70c23f694412895b72a383001c0f2`, data-source ID `dfd97a4e-0114-4cb8-8f75-658bb2b83b17` (overridable via `NOTION_RESEARCH_DATA_SOURCE_ID`)
  - Schema migrations: `pipeline/scripts/migrate_notion_blog_schema.py` (adds `Status`, `Excerpt`, `WordPress URL`), `pipeline/scripts/migrate_notion_schema.py` (research DB additions like `Reachable`, `Reachability Reason`, `Alt Source URL`). Notion-client wrapper defensively drops unknown properties when migrations have not been applied.

**Telegram (notifications + HITL):**
- Telegram Bot API via `curl` POST to `https://api.telegram.org/bot${BOT_TOKEN}/sendMessage` + long-poll `getUpdates` (`telegram_io.sh`, wrapped by `pipeline/telegram.py`)
  - Auth: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`
  - Caller env vars override `.env` per-call so the daily-summarizer can route messages to a dedicated channel (`TELEGRAM_DAILY_SUMMARY_CHAT_ID`) without polluting the main pipeline chat
  - Optional `TELEGRAM_PARSE_MODE=HTML` enables `<a href="...">link</a>` rendering
  - Sandbox profile auto-prefixes every message with `[SANDBOX] ` via `TELEGRAM_MESSAGE_PREFIX`
  - Uses long-poll `timeout=30` for replies; wall-clock timeout enforced from Python via `subprocess.timeout` so the pipeline doesn't hang forever
- Used for:
  - Run intake notifications (`📥`)
  - kb_curator HITL theme/framework approval (Stage 3 `interrupt()`)
  - Persistent error escalation (`retry / abort`)
  - Stage 7 validator PASS/PARTIAL/FAIL run summary
  - Daily-brief per-cluster Telegram message with `Listen` link to NotebookLM audio overview
  - Watchdog stale-brief alerts (`pipeline/scripts/check_daily_brief_freshness.sh`)

**NotebookLM (audio overview):**
- NotebookLM via the `nlm` CLI (NotebookLM-CLI). No direct REST integration; the CLI is invoked as a subprocess (`pipeline/summarize_daily/notebooklm_publisher.py`).
  - Auth: `nlm -p <NLM_PROFILE>` profile (env var `NLM_PROFILE`, default `painforwisdom`); profile credentials are managed by the CLI itself.
  - Commands shelled out: `nlm notebook create`, `nlm source add`, `nlm audio create --format deep_dive --length long --source-ids ... --focus "<prompt>" --confirm`, `nlm studio status [--full --json]`.
  - Per-theme notebook persistence: `briefs/<theme>/.notebooklm-id`. Each cluster gets a 4th source (the vault entry) so the audio is grounded in Gonzalo's raw voice.
  - Audio format: deep-dive, `--length long` (~40 min target) — see memory note on audio overview format.
  - No download; user listens via the NotebookLM mobile app. Pipeline writes `notebooklm_url.txt` + `audio_artifact_id.txt` to the cluster dir.
  - Failure handling: rate-limit exponential backoff (5/15/30 min) on Pro tier; auth-expired triggers Telegram alert + halt; render-timeout writes `audio_pending.txt` and succeeds.

**YouTube (short uploads — dormant by default):**
- YouTube Data API v3 via `google-api-python-client` (`pipeline/youtube_client.py`, `pipeline/nodes/youtube_upload.py`)
  - Auth: long-lived refresh-token OAuth captured once via `scripts/youtube_oauth_setup.py`. Env vars: `YOUTUBE_CLIENT_ID`, `YOUTUBE_CLIENT_SECRET`, `YOUTUBE_REFRESH_TOKEN`.
  - Scope: `https://www.googleapis.com/auth/youtube.upload`
  - Token endpoint: `https://oauth2.googleapis.com/token`
  - Gating: `YOUTUBE_ENABLED=true` activates the node; otherwise it writes a bundle to disk and skips the API call (no browser/UI at runtime).
  - Upload state: `privacyStatus="private"`, `selfDeclaredMadeForKids=False` — closest analog to a "draft" short on the `@painforwisdom` channel.
  - One-shot setup secrets file: `~/.config/painforwisdom/youtube_client_secret.json` (overridable via `YOUTUBE_CLIENT_SECRETS_PATH`)

**WordPress (blog drafts — dormant by default):**
- WordPress.com REST API v2 via `httpx` (`pipeline/wordpress_client.py`, `pipeline/nodes/wordpress_draft.py`)
  - Base URL: `https://public-api.wordpress.com/wp/v2/sites/<site>` (default site `painforwisdom.wordpress.com`, overridable via `WORDPRESS_SITE`)
  - Two auth modes via `WORDPRESS_AUTH_MODE`:
    - `app_password` (default) — HTTP Basic with `WORDPRESS_USERNAME` / `WORDPRESS_APP_PASSWORD` (paid WP.com plan required)
    - `oauth2` — Bearer `WORDPRESS_OAUTH_TOKEN`
  - Gating: `WORDPRESS_ENABLED=true` activates the node; otherwise emits a dormant bundle (`post.md`, `post.html`, `meta.json`) to `processed/.../wordpress/` for manual paste.
  - Endpoints used: `POST /media` (binary featured image), `POST /posts` with `status="draft"`, `GET/POST /tags` (resolve-or-create tag IDs), `POST /posts/<id>` (backfill amendments).

**Z-Library (book sourcing — optional bridge):**
- Z-Library via `zlibrary-mcp` subprocess bridge (`pipeline/zlibrary_bridge.py`, `pipeline/local_books.py`, `pipeline/agents/zlibrary-downloader/`)
  - Auth: `ZLIBRARY_EMAIL`, `ZLIBRARY_PASSWORD` (consumed by the bridge's own `.env`)
  - Config: `ZLIBRARY_REPO_PATH` must point at a local `zlibrary-mcp` checkout; absence makes the bridge return `BookFailure("not-configured")` and the pipeline degrades gracefully.
  - Bridge invocation: `subprocess.run([<repo>/.venv/bin/python, <repo>/lib/python_bridge.py, ...])` with `cwd=<repo>/lib` (sibling imports).
  - Extracted text is moved into `books/extracted/` (overridable via `PAINFORWISDOM_BOOKS_EXTRACTED`).

**AnythingLLM (cross-post context — stub backend):**
- Currently a stub (`pipeline/blog_context/anythingllm_backend.py`). Default backend is the local vault scanner (`pipeline/blog_context/vault_backend.py`).
- Selection via `BLOG_CONTEXT_BACKEND=vault|anythingllm` (default `vault`). Selecting `anythingllm` without all three env vars set silently falls back to vault with a `[blog-context] WARN`.
- Future env vars: `ANYTHINGLLM_BASE_URL` (e.g. `http://localhost:3001`), `ANYTHINGLLM_API_KEY`, `ANYTHINGLLM_WORKSPACE`. Per `REPORT.md`, swap will be hot once the AnythingLLM workspace is stable.
- Note: `.claude/agents/notion-research-logger.md` references AnythingLLM as the manual deep-dive workflow Gonzalo runs on Notion-logged research tasks.

**Obsidian Vault (git submodule, not an API):**
- `obsidian-vault/` is a git submodule pinned to branch `draft` (`.gitmodules`) of `https://github.com/gonandrap/painforwisdom-kb.git`
- Pipeline writes are file-system operations (new entries, theme updates, framework updates, research-index appends). Commits land on the `draft` branch and are visible in Obsidian immediately.
- Overridable via `VAULT_PATH` (sandbox profile points at `obsidian-vault-sandbox/` worktree).
- Vault subtree consumed: `obsidian-vault/gonzalo-book/{entries,themes,frameworks,research-index.md,book-outline.md,_index.md}`.

## Data Storage

**Databases:**
- SQLite (file-backed)
  - `pipeline/checkpoints.db` — LangGraph `SqliteSaver` for HITL `interrupt()` durability (`pipeline/graph.py`). Path overridable via `CHECKPOINT_DB_PATH`. Sandbox profile uses a separate file.
  - `pipeline/state/themes.db` — pipeline-local themes DB, seeded via `pipeline/scripts/seed_themes_db.py`, used by `pipeline/themes_db.py` + kb_curator.

**File Storage:**
- Local filesystem only.
  - `processed/<RUN_ID>/<suffix>/` — per-run output bundles (transcript, extraction report, vault entry diffs, blog_post.md, research_report.csv, audit_report.md, runs.jsonl telemetry trace, optional WordPress dormant bundle, optional YouTube bundle)
  - `briefs/<theme>/<date>--<slug>/` — per-cluster daily-brief outputs (deep-dive.md, application.md, audio-prompts.md, notebooklm_url.txt, audio_artifact_id.txt or audio_pending.txt)
  - `to_be_retried/` — failed transcripts queued for retry
  - `books/extracted/` — z-library extracted text
  - `data/` — watchdog state files (`.daily_brief_watchdog.last_alert`, `daily_brief_watchdog.log`)

**Caching:**
- In-process: `pipeline/notion_client.py` caches blog DB property set for the process lifetime (`_BLOG_SCHEMA_CACHE`). LLM cache_control ephemeral blocks on the system prompt (Sonnet 4.6 cache floor empirically ~2,048 tokens — see `pipeline/runtime.py:CACHE_TOKEN_FLOOR`).

## Authentication & Identity

**Anthropic:** OAuth subscription via `claude setup-token` (preferred, `$0` marginal) OR API key. Token rotation re-read from `~/.claude/.credentials.json` on every call and on auth-error retry.

**Notion:** Internal integration token (`NOTION_API_KEY`). Both target DBs must be shared with the integration.

**Telegram:** Bot token + chat ID. Bot must be added to the target chat(s).

**OpenAI (Whisper API fallback only):** `OPENAI_API_KEY` (note: only the helper-script fallback uses it; primary path is local Whisper).

**YouTube:** Long-lived refresh-token OAuth captured by `scripts/youtube_oauth_setup.py` (test-user grant on Gonzalo's Google account). Refresh exchange happens silently per upload.

**WordPress:** Either WP.com application password (Basic) or OAuth2 bearer. Free WP.com plan blocks writes; pipeline defaults to dormant.

**NotebookLM:** Handled by the `nlm` CLI profile system; no env vars passed by the pipeline beyond `NLM_PROFILE`.

**Z-Library:** Email + password in the bridge's own `.env` (not the pipeline's `.env`).

## Monitoring & Observability

**Error Tracking:**
- Telegram is the primary alert surface. No Sentry / Datadog / Rollbar integration.
- Persistent node failures escalate to Telegram with `retry / abort`; transient errors retry per `pipeline/graph.py:_RETRY_POLICY` (3 attempts, exponential backoff, jittered).

**Logs:**
- Per-run JSONL telemetry at `processed/<RUN_ID>/<suffix>/runs.jsonl` (every node appends a row via `pipeline/runtime.py:append_metric`).
- Daily-summarizer telemetry at `reports/daily-summarizer-runs.jsonl`.
- Watchdog log at `data/daily_brief_watchdog.log`.
- systemd-journald for the daily-brief unit (`journalctl --user -u painforwisdom-daily-brief.service`).
- No centralized log aggregation.

## CI/CD & Deployment

**Hosting:** Single Linux developer host. No remote deploy target.

**CI Pipeline:** None detected in this repo. Test runs are manual (`tests/smoke_pipeline.sh`).

**Scheduled jobs (systemd user units, not crontab):**
- `painforwisdom-daily-brief.timer` → `.service` at `~/.config/systemd/user/`. Fires daily 06:00 local. Calls `python -m pipeline.summarize_daily --apply --mcp-publish --max-cost-usd 1.0 --count 3`. Retry budget: 3 starts in 2h (per memory note on operations).
- Cron-driven watchdog: `pipeline/scripts/check_daily_brief_freshness.sh` runs from cron (deliberately, so it survives systemd-user failures), alerts via Telegram if newest `briefs/<theme>/<date>--<slug>/` is older than `MAX_AGE_HOURS` (25h), dedupes alerts via `data/.daily_brief_watchdog.last_alert` over `DEDUPE_HOURS` (12h).

## Environment Configuration

**Required env vars (prod profile, `.env`):**
- `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` — notifications + HITL
- `OPENAI_API_KEY` — Whisper API fallback only
- `NOTION_API_KEY` — Notion DB writes
- `ANTHROPIC_AUTH_TOKEN` (subscription) OR `ANTHROPIC_API_KEY` (credits)

**Optional env vars (feature flags / overrides):**
- `NOTION_BLOG_DATA_SOURCE_ID`, `NOTION_RESEARCH_DATA_SOURCE_ID` — override prod DB IDs (sandbox uses this)
- `TELEGRAM_DAILY_SUMMARY_CHAT_ID` — route daily briefs to a dedicated channel
- `TELEGRAM_PARSE_MODE` — `HTML` or `MarkdownV2` for rich-text rendering
- `TELEGRAM_MESSAGE_PREFIX` — auto-set to `[SANDBOX] ` under sandbox profile
- `VAULT_PATH` — sandbox vault override
- `CHECKPOINT_DB_PATH` — sandbox LangGraph checkpoint DB override
- `PIPELINE_MODEL` — override default `claude-sonnet-4-6`
- `WHISPER_BACKEND` (`local`/`openai`/`auto`), `WHISPER_MODEL` (default `medium`), `WHISPER_DEVICE` (override CUDA / CPU)
- `WORDPRESS_ENABLED=true` to activate WP draft creation; `WORDPRESS_AUTH_MODE`, `WORDPRESS_USERNAME`, `WORDPRESS_APP_PASSWORD`, `WORDPRESS_OAUTH_TOKEN`, `WORDPRESS_SITE`
- `YOUTUBE_ENABLED=true` to activate uploads; `YOUTUBE_CLIENT_ID`, `YOUTUBE_CLIENT_SECRET`, `YOUTUBE_REFRESH_TOKEN`, `YOUTUBE_CLIENT_SECRETS_PATH`
- `NLM_PROFILE` (default `painforwisdom`), `LISTENER_NAME`, `LISTENER_BIO` — NotebookLM publisher
- `BLOG_CONTEXT_BACKEND` (`vault` default, `anythingllm` stub), `ANYTHINGLLM_BASE_URL`, `ANYTHINGLLM_API_KEY`, `ANYTHINGLLM_WORKSPACE`
- `ZLIBRARY_REPO_PATH`, `ZLIBRARY_EMAIL`, `ZLIBRARY_PASSWORD`, `PAINFORWISDOM_BOOKS_EXTRACTED`

**Secrets location:**
- `.env` and `.env.sandbox` in repo root (gitignored)
- `~/.claude/.credentials.json` (managed by `claude` CLI; never committed)
- `~/.config/painforwisdom/youtube_client_secret.json` (YouTube OAuth client secret)
- `nlm` CLI profile store (managed by NotebookLM-CLI itself)

## Webhooks & Callbacks

**Incoming:** None. The pipeline is a pure outbound integrator; no HTTP server is exposed.

**Outgoing:**
- Telegram `sendMessage` to a known chat (notification, HITL prompt)
- Notion `pages.create` / `pages.update` / `data_sources.query` / `blocks.children.list`
- WordPress `/posts`, `/media`, `/tags` (REST writes, when `WORDPRESS_ENABLED=true`)
- YouTube v3 `videos.insert` (when `YOUTUBE_ENABLED=true`)
- NotebookLM `nlm audio create` etc. via CLI subprocess
- Anthropic `messages` (LiteLLM-mediated)
- Optional research HEAD/GET fetches (via httpx + trafilatura) for paywall heuristics and URL verification

---

*Integration audit: 2026-05-18*
