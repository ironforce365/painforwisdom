# Architecture

**Analysis Date:** 2026-05-18

## Pattern Overview

**Overall:** LangGraph DAG (directed-acyclic node graph) orchestrated as plain Python, with SQLite checkpointing for durable resume across human-in-the-loop pauses and process restarts.

**Key Characteristics:**
- One TypedDict `State` shared by all nodes; partial-update merge semantics via LangGraph reducers (`pipeline/state.py`).
- Fan-out / fan-in topology rooted at `extract`, with three independent downstream branches that re-converge at `wordpress_draft` and `validator`.
- All file I/O is owned by pipeline nodes — LLM agent prompts under `.claude/agents/` are loaded as system prompts but the file-writing instructions in those prompts are stripped (`pipeline/runtime.py:load_agent_prompt`); the model returns inline structured text and the node writes to disk.
- External side effects are isolated per node: `notion_client.py` (REST), `extract_transcription.sh` (Whisper CLI), `wordpress_client.py` (REST), `youtube_client.py` (Google API), `telegram_io.sh` (curl). LLM access is funneled through `pipeline/llm.py` (LiteLLM + OAuth subscription auth).
- Two distinct top-level pipelines: per-video content pipeline (`pipeline.run`) and the daily research-brief pipeline (`pipeline.summarize_daily`). They share the `State`/telemetry conventions but have separate graphs and CLIs.

## Layers

**Orchestration layer:**
- Purpose: CLI entry, profile selection, batch driving, HITL/error round-trips.
- Location: `pipeline/run.py`, `pipeline/retry.py`
- Contains: argparse, env-profile dispatch (`prod` vs `sandbox`), per-video looping, Telegram reply loops, video archive/quarantine moves, run-id minting.
- Depends on: `pipeline.graph`, `pipeline.telegram`, `pipeline.contracts`.
- Used by: human operators, systemd timers, `/retry-failed` Claude skill.

**Graph layer:**
- Purpose: Declares the DAG topology, retry policy, checkpointer.
- Location: `pipeline/graph.py`
- Contains: `build_graph(start_at)` factory, `_is_transient` classifier, `RetryPolicy` (3 attempts, exponential 2s→60s, jitter), `SqliteSaver` wiring to `pipeline/checkpoints.db` (or `checkpoints-sandbox.db`).
- Depends on: every `pipeline/nodes/*.py`, `pipeline.state`.
- Used by: `pipeline.run`, `pipeline.retry`.

**Node layer:**
- Purpose: One file per stage; each is a pure function `State -> Dict[str, Any]` that updates state.
- Location: `pipeline/nodes/*.py`
- Contains: stage I/O, prompt loading, parsing, on-disk artifact writes under `processed/<run_id>/<run_suffix>/<stage>/`.
- Depends on: `pipeline.contracts.assert_inputs`, `pipeline.llm.call_llm`, `pipeline.runtime`, side-effect clients (`pipeline.notion_client`, `pipeline.wordpress_client`, `pipeline.youtube_client`, `pipeline.image_extractor`).
- Used by: `pipeline.graph`.

**Service-client layer:**
- Purpose: Thin wrappers around external systems. No business logic, no orchestration.
- Files:
  - `pipeline/llm.py` — LiteLLM wrapper + OAuth-token refresh from `~/.claude/.credentials.json`; subscription vs API-credit billing modes; opt-in 1M-context beta header (`context-1m-2025-08-07`) per-call.
  - `pipeline/notion_client.py` — Notion REST via `notion-client` SDK; schema caching; rate-paced at 0.4s per page.
  - `pipeline/wordpress_client.py` — WP REST for draft post creation; dormant-mode bundle writer.
  - `pipeline/youtube_client.py` — YouTube Data API v3 upload.
  - `pipeline/image_extractor.py` — OpenCV + ffmpeg smart-frame picker.
  - `pipeline/telegram.py` — Subprocess wrapper for `telegram_io.sh`.
  - `pipeline/token_rotation.py` — OAuth credentials file reader.
  - `pipeline/banned_sources.py`, `pipeline/local_books.py`, `pipeline/zlibrary_bridge.py` — research helpers.

**Shared-state / cross-cutting:**
- `pipeline/state.py` — TypedDict for the shared state object.
- `pipeline/contracts.py` — per-node input contract assertions; raise `InputContractError` (treated as non-retryable).
- `pipeline/runtime.py` — `load_agent_prompt`, `append_metric`, `date_from_filename`, `slugify`, `parse_extraction_field`, `VAULT_PATH` resolution from env, `CACHE_PADDING_APPENDIX` (EXECUTION CONTEXT block appended to every system prompt to clear the Sonnet 4.6 cache-engagement floor of ~2200 tokens).
- `pipeline/themes_db.py` — SQLite cache over `data/themes.yaml` (theme → research agent routing).

**Daily-brief subsystem (separate pipeline, shared runtime):**
- Location: `pipeline/summarize_daily/`
- Files:
  - `__main__.py` — CLI; picks N clusters, runs each through `_run_one_brief`, sends Telegram per brief, marks rows Summarized in Notion.
  - `clusterer.py` — Notion → row selection; `pick_cluster` with skip-themes / excluded-page-ids filter.
  - `fetcher.py` — URL → cleaned text via trafilatura; honors `config/fetch_denylist.txt`.
  - `brief_writer.py` — Delegates to `pipeline/scripts/poc_brief_v2.run_cluster` to render `deep-dive.md` + `application.md` + `audio-prompts.md`.
  - `notebooklm_publisher.py` — Uploads brief bundle to NotebookLM via `nlm` CLI; returns notebook URL.
  - `notion_state.py` — Marks consumed Research Tasks rows as Summarized.
- Entry point: `python -m pipeline.summarize_daily --apply --mcp-publish --count 3`.

## Data Flow

### Per-video content pipeline (`python -m pipeline.run --video <mp4>`)

```
START → transcribe → extract ─┬─▶ kb_curator ─┬─▶ writer ──▶ notion_blog ──┐
                              │               └─▶ research ▶ notion_research ──┐
                              ├─▶ extract_image ────────────────────────┐      │
                              └─▶ youtube_upload ───────────────────────┼──────┤
                                                                        ▼      │
                                                              wordpress_draft  │
                                                                        │      │
                                                                        ▼      ▼
                                                                       validator → END
```

Ordered flow (file references in backticks):

1. **transcribe** (`pipeline/nodes/transcribe.py`) — shells `extract_transcription.sh`; local Whisper first, transparent CPU fallback on CUDA OOM with Telegram alert; writes `<video-dir>/auto-generated/transcript_YYYY-MM-DD.txt`; sets `transcript_text`, `transcript_path`, `transcript_word_count`, `video_date`.
2. **extract** (`pipeline/nodes/extract.py`) — system prompt = `.claude/agents/coaching-thought-extractor.md` stripped + `_EXTRACT_OUTPUT_SPEC` + `CACHE_PADDING_APPENDIX`. Sonnet 4.6 returns a structured extraction report; node parses `Content Quality`, `Core Insight`, `Story Anchor`, `Blog Post Seed`, `Themes`, `Frameworks`. Writes `<run_dir>/coaching-thought-extractor/extraction_report.md`.
3. **Fan-out (3 siblings):**
   - **kb_curator** (`pipeline/nodes/kb_curator.py`) — emits YAML-fenced curation plan inside `---kb-plan---` markers; one of `PROCEED` / `NEEDS_APPROVAL_THEME` / `NEEDS_APPROVAL_FRAMEWORK`. On approval-needed, calls `langgraph.types.interrupt(...)` to suspend the graph. On `PROCEED`, writes the vault entry (`obsidian-vault/gonzalo-book/entries/<YYYY-MM-DD-slug>.md`), updates touched theme/framework files, appends a row to `_index.md`, rewrites `book-outline.md`. Pre-approved exception: `pattern-manifestation` theme is auto-attached to `Flagged` entries without an approval round-trip.
   - **extract_image** (`pipeline/nodes/extract_image.py`) — OpenCV Laplacian variance + brightness scoring; writes `<run_dir>/wordpress-draft/featured.jpg`. Self-skips on `validator_verdict == "FAIL"` or missing video. No retry policy — degrades to `image_extraction_failed=True`.
   - **youtube_upload** (`pipeline/nodes/youtube_upload.py`) — first calls `.claude/agents/youtube-upload-agent.md` for title/description/extra tags; merges with `config/youtube_metadata.json` defaults; uploads as draft (privacy=private) via `pipeline/youtube_client.py`. Self-skips if `YOUTUBE_ENABLED != "true"` (dormant by default; metadata bundle still written).
4. **writer** (`pipeline/nodes/writer.py`) — system prompt = `.claude/agents/painforwisdom-writer.md`. Pulls cross-post context via `pipeline/blog_context/get_backend()` (vault or AnythingLLM). Returns blog body + `**Excerpt:**` line. Writes `<run_dir>/painforwisdom-writer/blog_post.md`.
5. **research** (`pipeline/nodes/research.py`) — system prompt = `.claude/agents/research-curator.md` with Anthropic native `web_search_20250305` tool attached. Returns CSV; HEAD-checks URLs via httpx + trafilatura paywall detection. Writes `<run_dir>/research-curator/research_report.csv`.
6. **notion_blog** (`pipeline/nodes/notion_blog.py`) — pure REST via `pipeline/notion_client.create_blog_page`. Creates page in "Blog post pending publications" data source (id resolved from `NOTION_BLOG_DATA_SOURCE_ID` env or hardcoded prod default). Verifies the body landed by re-fetching blocks.
7. **notion_research** (`pipeline/nodes/notion_research.py`) — pure REST; one page per CSV row in "Research Tasks" DB; theme→agent routing via `pipeline.themes_db.get_agent`; paced at 0.4s/page.
8. **wordpress_draft** (`pipeline/nodes/wordpress_draft.py`) — joins on `notion_blog` (page id) and `extract_image` (featured.jpg). Renders HTML via `pipeline/wordpress_client.render_wp_html`. If `WORDPRESS_ENABLED != "true"`, writes the bundle to disk (dormant mode) for future replay. On success, updates the Notion blog page with the WordPress URL.
9. **validator** (`pipeline/nodes/validator.py`) — pure-Python audit (no LLM). Core checks (transcript, extraction report, vault entry, blog file, research CSV rows ≥1, Notion blog page non-empty, research task count matches CSV) gate `FAIL`. Secondary checks (vault entry sections, theme mtime, Telegram delivery, content quality classified) gate `PARTIAL`. Writes `<run_dir>/validator/audit_report.md` and `<run_dir>/pipeline-summary/pipeline_summary.md`. Sends Telegram summary with retry once.

### Daily-brief pipeline (`python -m pipeline.summarize_daily`)

1. `fetch_pending_rows` (`pipeline/summarize_daily/clusterer.py`) reads Notion Research Tasks DB, drops denylisted URLs.
2. `pick_cluster` picks the most-pending reachable cluster (theme + sub-angle), skipping themes already consumed this run.
3. `brief_writer.write_brief` fetches each row's URL (caches under `briefs/.cache/`), delegates to `pipeline/scripts/poc_brief_v2.run_cluster` to write 3 markdown files under `briefs/<theme>/<YYYY-MM-DD>--<sub-angle-slug>/`.
4. (optional) `notebooklm_publisher.publish` uploads the brief to NotebookLM via the `nlm` CLI; returns notebook URL.
5. `notion_state.mark_summarized` flips the consumed rows to Summarized in Notion.
6. Telegram message goes to dedicated channel via `TELEGRAM_DAILY_SUMMARY_CHAT_ID` (HTML parse-mode for `<a href>` links).
7. Loop until `--count N` briefs produced or no eligible cluster remains. Per-brief crash is caught and surfaced to Telegram so subsequent briefs still run.

### State Management

`pipeline/state.py` declares one TypedDict `State` with `total=False` so partial dict returns from nodes merge cleanly. The `metrics` field uses `Annotated[List[Dict], add]` so the parallel branches' telemetry appends are commutative under LangGraph's reducer. Disjoint state keys per branch (e.g. `featured_image_path` from extract_image, `youtube_url` from youtube_upload, `notion_blog_page_id` from notion_blog) ensure the parallel-update merge is conflict-free.

Per-run on-disk state lives under `processed/<run_id>/<run_suffix>/` (default suffix: `langgraph`), one subdir per stage:
```
processed/<run_id>/langgraph/
  coaching-thought-extractor/extraction_report.md
  kb-curator/curator_summary.md
  painforwisdom-writer/blog_post.md
  research-curator/research_report.csv
  notion-blog-post-logger/notion_blog_summary.md
  notion-research-logger/notion_research_summary.md
  wordpress-draft/featured.jpg + wordpress_bundle.json + WP_*.md
  youtube-upload/youtube_metadata.json (+ marker files)
  validator/audit_report.md
  pipeline-summary/pipeline_summary.md
  runs.jsonl                     # per-stage telemetry
```

LangGraph checkpoint state lives in `pipeline/checkpoints.db` (prod) / `pipeline/checkpoints-sandbox.db` (sandbox), keyed by `thread_id = run_id`. This is the durable channel for HITL `interrupt()` resume across process restarts (`pipeline/retry.py`).

## Key Abstractions

**State (TypedDict):**
- Purpose: Single shared dict mutated by every node.
- Definition: `pipeline/state.py`
- Pattern: `total=False` so every key is optional; nodes return partial dicts which LangGraph merges.

**Node function:**
- Purpose: Pure stage logic, single entry point per stage.
- Examples: `pipeline/nodes/extract.py:node_extract`, `pipeline/nodes/kb_curator.py:node_kb_curator`, …
- Pattern: signature `def node_<name>(state: State) -> Dict[str, Any]`; first line is always `assert_inputs("<name>", state)` per the contract registry in `pipeline/contracts.py:NODE_INPUTS`.

**Agent prompt:**
- Purpose: Conceptual carry-over from the legacy Paperclip orchestrator — each node still loads its system prompt from a markdown file under `.claude/agents/`.
- Examples: `.claude/agents/coaching-thought-extractor.md`, `.claude/agents/kb-curator.md`, `.claude/agents/painforwisdom-writer.md`, `.claude/agents/research-curator.md`, `.claude/agents/notion-blog-post-logger.md`, `.claude/agents/notion-research-logger.md`, `.claude/agents/youtube-upload-agent.md`, `.claude/agents/pipeline-summary.md`.
- Pattern: `pipeline/runtime.py:load_agent_prompt` strips the YAML frontmatter and the `## OUTPUT` section (those instructions assume Bash/Write tool access the LangGraph node does not give the model) and appends `CACHE_PADDING_APPENDIX` so the prompt clears the empirical Sonnet 4.6 cache floor of ~2200 tokens.

**Curation plan (kb_curator):**
- Purpose: Single structured payload the LLM emits, parsed by the node to drive vault writes.
- Definition: YAML inside `---kb-plan---` markers. See `pipeline/nodes/kb_curator.py:_KB_OUTPUT_SPEC`.
- Pattern: One of three `action` values (`PROCEED`, `NEEDS_APPROVAL_THEME`, `NEEDS_APPROVAL_FRAMEWORK`). On approval-needed, the node calls `interrupt()` and the orchestrator handles the Telegram round-trip.

## Entry Points

**Per-video pipeline CLI:**
- Location: `pipeline/run.py`
- Invocation: `python -m pipeline.run --video <mp4>` | `--dir <videos-dir>` | `--from-transcript <txt>`
- Profile selection: `--profile prod` (loads `.env`) or `--profile sandbox` (loads `.env.sandbox`, prefixes Telegram with `[SANDBOX] `, redirects to sandbox vault + checkpoint DB).
- Triggers: Manual operator runs, `/extract-transcription` skill (transcription only), `/retry-failed` skill (for `to_be_retried/`).

**Retry / resume CLI:**
- Location: `pipeline/retry.py`
- Invocation: `python -m pipeline.retry [--run-id <id>] [--video <mp4>] [--quarantine bulk/quarantine]`
- Triggers: After a quarantined batch run. Default mode scans `bulk/quarantine/`, matches each video to its checkpoint thread, and resumes via `graph.invoke(None, config={"thread_id": run_id})`.

**Daily-brief CLI:**
- Location: `pipeline/summarize_daily/__main__.py`
- Invocation: `python -m pipeline.summarize_daily {--dry-run|--apply} [--mcp-publish] [--count N]`
- Triggers: `painforwisdom-daily-brief.timer` systemd user unit at 06:00 local daily.

**Watchdog (cron, not systemd):**
- Location: `pipeline/scripts/check_daily_brief_freshness.sh`
- Schedule: Cron (so it survives the same failure modes that would kill a systemd timer).
- Logic: Alerts via Telegram if newest `briefs/<theme>/<date>--<slug>/` mtime older than `MAX_AGE_HOURS=25h`, AND/OR if `systemctl --user is-active painforwisdom-daily-brief.timer` is not `active`. Heal-then-notify: attempts a `systemctl --user start` before alerting. Dedupes via `data/.daily_brief_watchdog.last_alert` (12h window). Alerts route to the dedicated `daily_summary` Telegram channel.

**Auxiliary scripts:**
- `python -m pipeline.smoke_notion` — verify Notion API auth + DB schema.
- `python -m pipeline.cost_forecast --transcript <txt>` / `--video <mp4>` — token+$+quota forecast.
- `python -m pipeline.scripts.audit_research_tasks` — Notion research-row audit.
- `python -m pipeline.scripts.seed_themes_db` — apply `data/themes.yaml` to `pipeline/state/themes.db`.
- `python -m pipeline.scripts.render_curator_taxonomy --apply` — regenerate the taxonomy block in `.claude/agents/research-curator.md`.
- `bash extract_transcription.sh <video> <lang> <date>` — standalone Whisper helper (also used by Stage 1).
- `bash telegram_io.sh {send|ask|wait_reply} ...` — Telegram I/O primitive.
- `bash tests/smoke_pipeline.sh` / `bash tests/sandbox_reset.sh` — sandbox driver + reset.

## Error Handling

**Strategy:** Two-tier — transient infra errors retried in-graph by LangGraph's `RetryPolicy`; persistent errors bubble to the orchestrator (`pipeline/run.py:_drive_graph`), which escalates to Telegram with a bounded reminder loop and accepts `retry` / `abort` replies.

**Patterns:**
- **Transient classifier:** `pipeline/graph.py:_is_transient` returns True for `httpx.HTTPError`, `socket.timeout`, `ConnectionError`, `subprocess.TimeoutExpired`, and litellm/Notion 5xx-class exceptions. `InputContractError` and everything else is treated as persistent.
- **RetryPolicy:** 3 attempts, 2s → 60s exponential backoff with jitter, applied to `transcribe`, `extract`, `kb_curator`, `writer`, `research`, `notion_blog`, `notion_research`. The newer parallel nodes (`extract_image`, `youtube_upload`, `wordpress_draft`) intentionally carry no retry policy — they catch their own errors and degrade gracefully (set `*_skipped=True` or `image_extraction_failed=True`).
- **Non-recoverable classifier:** `pipeline/run.py:_classify_non_recoverable` surfaces specific error strings (Anthropic "Extra usage is required for long context requests", auth-credential rejection) and tells the user `retry will fail identically — reply abort`.
- **HITL approval loop:** kb_curator's `interrupt()` → `_get_pending_interrupt` → `_ask_indefinitely` (reposts the prompt every `--reminder-interval` seconds, defaults 30 min, waits forever). The orchestrator resumes the graph with `Command(resume=<reply text>)`.
- **Error-recovery loop:** Bounded — `_ask_bounded(prompt, reminder_interval, max_reminders=5)` so a stuck pipeline does not block forever waiting for a Telegram reply on an error prompt.
- **CUDA OOM fallback:** `pipeline/nodes/transcribe.py` detects `CUDA out of memory` in Whisper stderr and re-invokes with `WHISPER_DEVICE=cpu`. Notifies Telegram that wall-clock will balloon ~10×.
- **Token rotation:** `pipeline/llm.py:_refresh_auth` re-reads `~/.claude/.credentials.json` on every call (and again on 401). Lets the `claude` CLI rotate the OAuth token mid-run without breaking the pipeline.

## Cross-Cutting Concerns

**Logging / Telemetry:**
- Per-stage JSONL appended to `<run_dir>/runs.jsonl` via `pipeline/runtime.py:append_metric`. One row per stage: duration, tokens (input/output/cache_read/cache_creation), billing mode, model, plus stage-specific fields.
- Daily-summarizer telemetry appended to `reports/daily-summarizer-runs.jsonl`.
- Watchdog status appended to `data/daily_brief_watchdog.log`.
- Stdout per-stage `[stage] start/done` lines for live tailing.

**Authentication & Identity:**
- Anthropic: OAuth subscription preferred (`ANTHROPIC_AUTH_TOKEN`, refreshed from `~/.claude/.credentials.json`); API-key fallback (`ANTHROPIC_API_KEY`). Subscription path requires `extra_headers={"anthropic-beta": "oauth-2025-04-20"}` and the `CLAUDE_CODE_IDENTITY` system block. Opt-in 1M-context tier (`context-1m-2025-08-07`) is per-call via the `long_context=True` kwarg to `call_llm`.
- Notion: `NOTION_API_KEY` (internal integration token, distinct from MCP/claude.ai auth). Data source UUIDs in env or hardcoded as prod fallbacks in `pipeline/notion_client.py`.
- Telegram: `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` (main channel) + `TELEGRAM_DAILY_SUMMARY_CHAT_ID` (daily-brief channel).
- WordPress: `WORDPRESS_*` creds; gated by `WORDPRESS_ENABLED=true` (dormant by default).
- YouTube: OAuth via `scripts/youtube_oauth_setup.py`; gated by `YOUTUBE_ENABLED=true`.

**Profile isolation (prod vs sandbox):**
- `.env` vs `.env.sandbox` (loaded by `pipeline/run.py:_peek_profile` BEFORE pipeline imports — pipeline modules read env at import time).
- Sandbox redirects `VAULT_PATH` to `obsidian-vault-sandbox/` (a git worktree of the same vault repo), uses sandbox Notion data sources, and uses `pipeline/checkpoints-sandbox.db`.
- Sandbox messages get `[SANDBOX] ` prefix via `TELEGRAM_MESSAGE_PREFIX`.

**Validation:**
- Per-node input contracts in `pipeline/contracts.py:NODE_INPUTS`. Missing required field → `InputContractError` → orchestrator escalates as non-recoverable.
- Final-stage audit in `pipeline/nodes/validator.py` — pure Python, no LLM, produces PASS/PARTIAL/FAIL verdict.

**Telegram integration points:**
- `pipeline/telegram.py` is the only Python wrapper; everything else calls `send()` or `ask()`. It shells out to `telegram_io.sh` (curl-based, no Telegram SDK dependency).
- Per-call `chat_id` override (used by daily summarizer to route to `daily_summary` channel without polluting `content_pipeline`).
- Per-call `parse_mode` override (HTML for the daily brief link rendering; plain text for everything else).
- Touchpoints: run intake, HITL approval prompt (kb_curator), error escalation prompt (`_drive_graph`), validator summary, batch summary, retry summary, CUDA OOM alert, daily-brief audio-ready message, brief-crash alert, fetch-failure alert, watchdog alert.

**Vault layout as data flow:**
- The vault under `obsidian-vault/gonzalo-book/` is the canonical content store. The pipeline writes into it and downstream sinks (Notion, WordPress, YouTube) are derived from it.
- Entries: `obsidian-vault/gonzalo-book/entries/YYYY-MM-DD-slug.md` — one per video, immutable after creation (kb_curator refuses overwrite).
- Themes: `obsidian-vault/gonzalo-book/themes/<slug>.md` — coarse buckets (~11 active themes per memory note "Vault vs Notion themes"); each has Core tension + Key insight + entry table. kb_curator rewrites the whole theme file on every entry that touches it.
- Frameworks: `obsidian-vault/gonzalo-book/frameworks/<slug>.md` — named conceptual models (e.g. `cookie-jar-types`, `friction-types`, `phase-1-protocol`).
- Timeline: `obsidian-vault/gonzalo-book/_index.md` — master timeline table; kb_curator appends one row per entry.
- Book outline: `obsidian-vault/gonzalo-book/book-outline.md` — auto-maintained synthesis; kb_curator rewrites the whole file as new patterns emerge across entries.
- Research index: `obsidian-vault/gonzalo-book/research-index.md` — verified references organized by topic.
- Submodule: pinned to the `draft` branch of `gonandrap/painforwisdom-kb` (see `.gitmodules`). Pipeline commits land on `draft`, manual merge to `main` after review.

---

*Architecture analysis: 2026-05-18*
