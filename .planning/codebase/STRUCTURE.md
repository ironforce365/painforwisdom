# Codebase Structure

**Analysis Date:** 2026-05-18

## Directory Layout

```
painforwisdom/
├── pipeline/                         # LangGraph DAG, nodes, runtime, daily-brief subsystem
│   ├── __init__.py                   # Pkg init; silences LangChain deprecation warning before any LG import
│   ├── run.py                        # CLI entry — `python -m pipeline.run` (per-video pipeline)
│   ├── retry.py                      # Resume failed runs from LangGraph checkpoints
│   ├── graph.py                      # DAG topology, RetryPolicy, SqliteSaver wiring
│   ├── state.py                      # Shared TypedDict `State`
│   ├── contracts.py                  # Per-node input contracts → `InputContractError`
│   ├── runtime.py                    # `load_agent_prompt`, `append_metric`, paths, slug/date helpers
│   ├── llm.py                        # LiteLLM wrapper, OAuth refresh, billing modes, long-context beta
│   ├── token_rotation.py             # Reads ~/.claude/.credentials.json
│   ├── telegram.py                   # Python → telegram_io.sh wrapper
│   ├── notion_client.py              # Notion REST helpers (notion-client SDK)
│   ├── wordpress_client.py           # WordPress REST + dormant-bundle writer
│   ├── youtube_client.py             # YouTube Data API v3 upload
│   ├── image_extractor.py            # OpenCV smart-frame picker (featured image)
│   ├── themes_db.py                  # SQLite cache over data/themes.yaml
│   ├── banned_sources.py             # Research source denylist matcher
│   ├── local_books.py                # Lookup books/extracted/*.md by topic
│   ├── zlibrary_bridge.py            # z-library acquisition helper
│   ├── backfill_wordpress.py         # One-off WP backfill of historical posts
│   ├── cost_forecast.py              # `python -m pipeline.cost_forecast` — token/$ forecast
│   ├── smoke_notion.py               # `python -m pipeline.smoke_notion` — pre-flight DB check
│   ├── forecast.md                   # Internal note: cost forecast methodology
│   ├── requirements.txt              # Python deps (langgraph, litellm, notion-client, etc.)
│   ├── checkpoints.db                # Prod LangGraph SqliteSaver (gitignored)
│   ├── checkpoints-sandbox.db        # Sandbox SqliteSaver (gitignored)
│   ├── nodes/                        # One file per pipeline stage
│   │   ├── __init__.py               # (empty)
│   │   ├── transcribe.py             # Stage 1 — Whisper via extract_transcription.sh; CUDA→CPU fallback
│   │   ├── extract.py                # Stage 2 — coaching-thought-extractor LLM call
│   │   ├── kb_curator.py             # Stage 3 — vault writes + HITL `interrupt()` for new themes/frameworks
│   │   ├── writer.py                 # Stage 4a — painforwisdom-writer blog post generation
│   │   ├── research.py               # Stage 4b — research-curator with Anthropic web_search tool
│   │   ├── extract_image.py          # Stage 4c — OpenCV featured-image pick (parallel branch)
│   │   ├── youtube_upload.py         # Stage 4d — YouTube Short draft upload (parallel branch)
│   │   ├── notion_blog.py            # Stage 5a — log blog post to Notion (pure REST)
│   │   ├── notion_research.py        # Stage 5b — log research tasks to Notion (pure REST)
│   │   ├── wordpress_draft.py        # Stage 5c — WordPress draft (joins notion_blog + extract_image)
│   │   └── validator.py              # Stage 6 — audit + Telegram summary (no LLM)
│   ├── summarize_daily/              # Daily research-brief subsystem (separate pipeline)
│   │   ├── __init__.py               # Pkg docstring describing layout
│   │   ├── __main__.py               # CLI — `python -m pipeline.summarize_daily`
│   │   ├── clusterer.py              # Notion → cluster selection (`pick_cluster`)
│   │   ├── fetcher.py                # URL → cleaned text via trafilatura; honors fetch_denylist.txt
│   │   ├── brief_writer.py           # Delegates to poc_brief_v2.run_cluster
│   │   ├── notebooklm_publisher.py   # `nlm` CLI wrapper; returns notebook + audio URL
│   │   └── notion_state.py           # Flip Research Tasks rows to Summarized
│   ├── blog_context/                 # Cross-post context backend (writer uses for "what did Gonzalo already say about X")
│   │   ├── __init__.py               # `get_backend()` dispatcher (BLOG_CONTEXT_BACKEND env)
│   │   ├── vault_backend.py          # Default: scans local obsidian-vault entries
│   │   └── anythingllm_backend.py    # Optional: AnythingLLM semantic-embedding workspace
│   ├── scripts/                      # One-off / scheduled scripts
│   │   ├── __init__.py
│   │   ├── audit_research_tasks.py
│   │   ├── augment_research_tasks.py
│   │   ├── build_theme_stats.py
│   │   ├── check_daily_brief_freshness.sh   # Cron watchdog (NOT systemd)
│   │   ├── migrate_notion_blog_schema.py
│   │   ├── migrate_notion_schema.py
│   │   ├── normalize_themes.py
│   │   ├── poc_brief.py                     # Legacy PoC daily-brief
│   │   ├── poc_brief_v1_ab.py               # A/B harness
│   │   ├── poc_brief_v2.py                  # `run_cluster` — current brief renderer (used by brief_writer)
│   │   ├── poc_brief_v2_round2.py
│   │   ├── render_curator_taxonomy.py       # Regen .claude/agents/research-curator.md taxonomy block
│   │   ├── run_augment_research.sh
│   │   └── seed_themes_db.py
│   └── state/                        # Derived runtime state (gitignored)
│       ├── themes.db                 # SQLite cache rebuilt from data/themes.yaml
│       └── theme_stats.json          # Theme usage counts for the curator
├── obsidian-vault/                   # Git submodule → gonandrap/painforwisdom-kb (branch: draft)
│   ├── .obsidian/                    # Obsidian app config (in submodule)
│   ├── gonzalo-book/                 # The canonical content store
│   │   ├── _index.md                 # Master timeline (markdown table; kb_curator appends rows)
│   │   ├── book-outline.md           # Auto-maintained book outline; rewritten by kb_curator
│   │   ├── research-index.md         # Verified references organized by topic
│   │   ├── entries/                  # One file per video: YYYY-MM-DD-slug.md (immutable)
│   │   ├── themes/                   # One file per theme: <slug>.md (~11 active)
│   │   └── frameworks/               # Named conceptual models: <slug>.md
│   └── Untitled.base                 # Obsidian base file
├── obsidian-vault-sandbox/           # Sandbox vault worktree of same submodule (gitignored)
├── .claude/                          # Claude Code config + agent prompts + skills
│   ├── agents/                       # System prompts loaded by pipeline nodes via load_agent_prompt
│   │   ├── coaching-thought-extractor.md  # Used by pipeline/nodes/extract.py
│   │   ├── kb-curator.md                  # Used by pipeline/nodes/kb_curator.py
│   │   ├── painforwisdom-writer.md        # Used by pipeline/nodes/writer.py
│   │   ├── research-curator.md            # Used by pipeline/nodes/research.py (taxonomy block auto-regen'd)
│   │   ├── notion-blog-post-logger.md     # Conceptual; notion_blog.py is pure REST (no LLM)
│   │   ├── notion-research-logger.md      # Conceptual; notion_research.py is pure REST (no LLM)
│   │   ├── youtube-upload-agent.md        # Used by pipeline/nodes/youtube_upload.py (metadata only)
│   │   ├── pipeline-summary.md            # Conceptual; validator.py is pure-Python
│   │   └── blog-post-catchy-title.md      # Deprecated — never produced good titles (see memory note)
│   ├── skills/
│   │   ├── extract-transcription/SKILL.md  # `/extract-transcription <video>` skill definition
│   │   └── retry-failed/SKILL.md           # `/retry-failed [transcript]` skill definition
│   ├── settings.local.json
│   └── scheduled_tasks.lock          # (gitignored)
├── agents/                           # External-agent home (not Claude Code subagents)
│   └── zlibrary-downloader/          # z-library acquisition agent workspace
├── books/                            # Z-library raw + extracted book text (gitignored)
│   ├── raw/                          # Raw downloads
│   ├── extracted/                    # Cleaned/extracted .md per book
│   ├── letters-from-a-stoic/
│   ├── peak-performance/
│   ├── prolonged-exposure-therapy-for-ptsd/
│   ├── rest-why-you-get-more-done-when-you-work-less/
│   ├── the-body-keeps-the-score/
│   └── the-psychology-of-passion-a-dualistic-model/
├── briefs/                           # Daily-brief output bundles (gitignored)
│   ├── .cache/                       # Per-source fetched text cache (sha256-keyed)
│   └── <theme-slug>/<YYYY-MM-DD>--<sub-angle-slug>/{deep-dive,application,audio-prompts}.md
├── bulk/                             # Pipeline input/output staging (gitignored)
│   ├── auto-generated/               # transcript_YYYY-MM-DD[_N].txt produced by Whisper
│   └── quarantine/                   # Failed-run videos awaiting retry via pipeline.retry
├── processed/                        # Per-run outputs (gitignored; one dir per run_id)
│   └── <run_id>/                     # run_id = YYYY-MM-DD_HHMMSS or *_NNN (batch-indexed)
│       ├── langgraph/                # Default run_suffix
│       │   ├── coaching-thought-extractor/
│       │   ├── kb-curator/
│       │   ├── painforwisdom-writer/
│       │   ├── research-curator/
│       │   ├── notion-blog-post-logger/
│       │   ├── notion-research-logger/
│       │   ├── wordpress-draft/      # featured.jpg + WP_*.md bundle
│       │   ├── youtube-upload/
│       │   ├── validator/audit_report.md
│       │   ├── pipeline-summary/pipeline_summary.md
│       │   └── runs.jsonl            # Per-stage telemetry (one JSON row per stage)
│       └── source/                   # Original video archived here after PASS/PARTIAL
├── tests/                            # Smoke harness, sandbox reset, transcript fixtures
│   ├── smoke_pipeline.sh             # Sandbox driver (configurable fixture)
│   ├── sandbox_reset.sh              # Vault revert + Notion archive
│   ├── fixtures/                     # Reusable transcripts for the smoke matrix
│   │   ├── README.md                 # Fixture matrix
│   │   ├── transcript_2026-04-14.txt
│   │   ├── transcript_2026-04-15-flagged.txt
│   │   ├── transcript_2026-04-16-weak.txt
│   │   └── transcript_2026-04-17-strong-existing-themes.txt
│   ├── test_banned_sources.py
│   ├── test_blog_context_vault.py
│   ├── test_contracts.py
│   ├── test_image_extractor.py
│   ├── test_local_books.py
│   ├── test_migration_idempotency.py
│   ├── test_research_node.py
│   ├── test_summarize_daily_fetch_resilience.py
│   ├── test_themes_db.py
│   └── test_wordpress_client.py
├── scripts/                          # Repo-level scripts (not packaged)
│   ├── scrape_youtube_tags.py
│   └── youtube_oauth_setup.py        # OAuth bootstrap for YouTube Data API
├── config/
│   ├── fetch_denylist.txt            # URLs the daily-brief fetcher must skip
│   └── youtube_metadata.json         # Default channel tag set merged with per-video extras
├── data/
│   ├── themes.yaml                   # Source of truth for pipeline/state/themes.db
│   ├── daily_brief_watchdog.log      # Cron watchdog status log
│   └── temp/                         # Local-draft scratch (gitignored)
├── reports/                          # Audit + run-telemetry JSONL/MD (gitignored)
│   ├── daily-summarizer-runs.jsonl
│   ├── augment-runs.jsonl
│   ├── research-audit-YYYY-MM-DD.{md,jsonl}
│   ├── sub-theme-proposal-YYYY-MM-DD.md
│   └── MORNING-YYYY-MM-DD.md
├── to_be_retried/                    # Failed transcripts queued for /retry-failed (gitignored)
├── extract_transcription.sh          # Whisper wrapper (used by Stage 1 + skill)
├── telegram_io.sh                    # Telegram send/ask/wait_reply primitive
├── OPERATIONS.md                     # Day-to-day commands (sandbox, smoke, debug, scheduled jobs)
├── README.md                         # Setup, topology, pipeline overview
├── REPORT.md                         # Internal pipeline-perf / migration report
├── .env                              # Prod profile env (gitignored; never read by mapper)
├── .env.sandbox                      # Sandbox profile env (gitignored)
├── .env.sandbox.template             # Tracked template for .env.sandbox
├── .paperclip.json                   # Legacy Paperclip orchestrator config (deprecated; gitignored)
├── .gitmodules                       # Pins obsidian-vault to gonandrap/painforwisdom-kb @ draft
├── .gitignore
└── .current_run                      # Last-run pointer file (gitignored)
```

## Directory Purposes

**`pipeline/`:**
- Purpose: All Python code for both the per-video pipeline and the daily-brief subsystem.
- Contains: Graph topology, node logic, runtime helpers, service clients, CLI entry points.
- Key files: `pipeline/run.py`, `pipeline/graph.py`, `pipeline/state.py`, `pipeline/contracts.py`, `pipeline/runtime.py`, `pipeline/llm.py`.

**`pipeline/nodes/`:**
- Purpose: One file per pipeline stage; each defines a `node_<name>(state) -> dict` function.
- Contains: Stage I/O, prompt building, parsing, on-disk artifact writes.
- Naming: Filename matches the node name in `pipeline/graph.py:build_graph` (e.g. `kb_curator.py` ↔ `g.add_node("kb_curator", ...)`).

**`pipeline/summarize_daily/`:**
- Purpose: Daily research-brief pipeline (decoupled from the per-video LangGraph DAG).
- Contains: Cluster picker, URL fetcher, brief renderer wrapper, NotebookLM publisher, Notion state updater.
- Triggered by: `painforwisdom-daily-brief.timer` systemd user unit.

**`pipeline/blog_context/`:**
- Purpose: Pluggable backend that answers "what has Gonzalo already said about X?" for the writer's cross-post context.
- Backends: `vault` (default), `anythingllm` (stub, falls back to vault if env not set).
- Selector: `BLOG_CONTEXT_BACKEND` env var.

**`pipeline/scripts/`:**
- Purpose: Manual / scheduled scripts. NOT part of the runtime graph.
- Contains: One-off migrations, theme regen, brief PoCs (note: `poc_brief_v2.run_cluster` is imported in production by `brief_writer.py`), the cron-driven watchdog shell script.

**`pipeline/state/`:**
- Purpose: Derived sqlite + JSON state (gitignored — rebuilt from `data/themes.yaml` on first access).
- Contains: `themes.db`, `theme_stats.json`.

**`obsidian-vault/`:**
- Purpose: Git submodule at `gonandrap/painforwisdom-kb` pinned to `draft`. The canonical content store.
- Pipeline writes: kb_curator writes entries + theme/framework updates + `_index.md` row + `book-outline.md` here.
- Sandbox parallel: `obsidian-vault-sandbox/` (same repo, separate worktree) selected via `VAULT_PATH` env.

**`obsidian-vault/gonzalo-book/`:**
- Purpose: The book project root inside the vault.
- Contents:
  - `_index.md` — Master timeline (markdown table; one row per entry).
  - `book-outline.md` — Auto-maintained synthesis across themes.
  - `research-index.md` — Verified references organized by topic.
  - `entries/` — Daily entries, immutable.
  - `themes/` — ~11 active themes (book chapters; intentionally coarse — see memory "Vault vs Notion themes").
  - `frameworks/` — Named conceptual models.

**`.claude/agents/`:**
- Purpose: Markdown agent prompts loaded as system prompts by pipeline nodes. Conceptual carry-over from the legacy Paperclip orchestrator; LangGraph nodes still reference these files by name through `pipeline/runtime.py:load_agent_prompt`.
- Pattern: Each file has YAML frontmatter (`name`, `description`, `model`, `tools`) and a long-form role description. `load_agent_prompt` strips the frontmatter and the trailing `## OUTPUT` section (which would tell the model to write files via Bash) before appending `CACHE_PADDING_APPENDIX`.
- Currently referenced by code: `coaching-thought-extractor.md`, `kb-curator.md`, `painforwisdom-writer.md`, `research-curator.md`, `youtube-upload-agent.md`.
- Conceptual only (no longer LLM-driven): `notion-blog-post-logger.md`, `notion-research-logger.md`, `pipeline-summary.md` (replaced by pure-Python nodes).
- Deprecated: `blog-post-catchy-title.md` (never produced good titles; stage removed from pipeline — see memory "blog-post-catchy-title dropped").

**`.claude/skills/`:**
- Purpose: Claude Code slash-command skills used as ergonomic wrappers.
- `extract-transcription/SKILL.md` — `/extract-transcription <video> [language] [date]` → runs `extract_transcription.sh`.
- `retry-failed/SKILL.md` — `/retry-failed [transcript.txt]` → processes `to_be_retried/`.

**`bulk/`:**
- Purpose: Input-staging dir for batch runs.
- `bulk/auto-generated/` — Whisper-produced transcripts staged for `pipeline.run --from-transcript`.
- `bulk/quarantine/` — Videos that failed batch processing, awaiting `pipeline.retry`.

**`processed/`:**
- Purpose: One directory per pipeline run. Contains per-stage outputs + telemetry. Original video archived to `processed/<run_id>/source/` on PASS/PARTIAL so successful runs are self-contained.

**`briefs/`:**
- Purpose: Daily-brief output. `briefs/<theme-slug>/<YYYY-MM-DD>--<sub-angle-slug>/` holds three markdown files (`deep-dive.md`, `application.md`, `audio-prompts.md`). `briefs/.cache/` holds per-URL fetched text keyed by sha256.

**`books/`:**
- Purpose: Z-library raw downloads + extracted text used by `pipeline/local_books.py` for offline research lookups.

**`tests/`:**
- Purpose: Smoke harness + unit tests + transcript fixtures.
- `smoke_pipeline.sh` — End-to-end sandbox run.
- `sandbox_reset.sh` — Idempotent reset of sandbox vault worktree + sandbox Notion DBs.
- `fixtures/` — Reusable transcripts covering the quality matrix (Strong / Weak / Flagged / Strong-existing-themes).

**`scripts/`:**
- Purpose: Repo-level operator scripts NOT packaged with `pipeline`. Run directly.

**`config/`:**
- Purpose: Static config consumed at runtime.
- `fetch_denylist.txt` — One URL per line; re-read on every daily-brief run (no restart needed).
- `youtube_metadata.json` — Channel-default tag set merged with per-video extras.

**`data/`:**
- Purpose: Cross-pipeline data + logs.
- `data/themes.yaml` — Source of truth for the themes registry (regenerated DB via `python -m pipeline.scripts.seed_themes_db`).
- `data/daily_brief_watchdog.log` — Append-only watchdog status log.

**`reports/`:**
- Purpose: Audit reports + scheduled-run telemetry. Append-only JSONL or daily-dated `.md`.

**`to_be_retried/`:**
- Purpose: Failed-transcript queue for the `/retry-failed` skill.

## Key File Locations

**Entry points:**
- `pipeline/run.py`: Per-video CLI — `python -m pipeline.run --video|--dir|--from-transcript`.
- `pipeline/retry.py`: Resume failed runs — `python -m pipeline.retry [--run-id|--video|--quarantine]`.
- `pipeline/summarize_daily/__main__.py`: Daily-brief CLI — `python -m pipeline.summarize_daily --apply --mcp-publish --count 3`.
- `pipeline/cost_forecast.py`: Pre-flight cost forecast.
- `pipeline/smoke_notion.py`: Pre-flight Notion auth/schema check.
- `extract_transcription.sh`: Whisper wrapper (auto-quarantines low-confidence transcripts).
- `telegram_io.sh`: Telegram primitive used by `pipeline/telegram.py`.

**Configuration:**
- `.env`: Prod profile (gitignored). Required: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `TELEGRAM_DAILY_SUMMARY_CHAT_ID`, `OPENAI_API_KEY`, `NOTION_API_KEY`, `ANTHROPIC_AUTH_TOKEN` or `ANTHROPIC_API_KEY`. Optional: `NOTION_BLOG_DATA_SOURCE_ID`, `NOTION_RESEARCH_DATA_SOURCE_ID`, `VAULT_PATH`, `CHECKPOINT_DB_PATH`, `WORDPRESS_ENABLED`, `YOUTUBE_ENABLED`, `BLOG_CONTEXT_BACKEND`, `PIPELINE_MODEL`.
- `.env.sandbox`: Sandbox profile (gitignored).
- `.env.sandbox.template`: Tracked template with placeholders.
- `pipeline/requirements.txt`: Python dependency manifest.
- `.gitmodules`: Pins `obsidian-vault` submodule to `gonandrap/painforwisdom-kb` @ `draft`.
- `config/youtube_metadata.json`: YouTube channel default metadata.
- `config/fetch_denylist.txt`: Daily-brief URL denylist.
- `data/themes.yaml`: Themes-registry source.

**Core logic:**
- `pipeline/graph.py`: DAG topology + RetryPolicy + checkpointer.
- `pipeline/state.py`: Shared state TypedDict.
- `pipeline/contracts.py`: Per-node input requirements.
- `pipeline/runtime.py`: `load_agent_prompt`, paths, telemetry helpers.
- `pipeline/llm.py`: LiteLLM wrapper, OAuth refresh, 1M-context opt-in.
- `pipeline/notion_client.py`: Notion REST helpers + hardcoded prod data-source IDs as fallback.
- `pipeline/themes_db.py`: SQLite cache over `data/themes.yaml`.

**Testing:**
- `tests/smoke_pipeline.sh`: Sandbox driver.
- `tests/sandbox_reset.sh`: Sandbox state reset.
- `tests/fixtures/`: Transcript fixtures matrix.
- `tests/test_*.py`: Pytest unit tests.

## Naming Conventions

**Pipeline stages (LangGraph node names):**
- Lowercase snake_case (`kb_curator`, `notion_blog`, `extract_image`, `wordpress_draft`).
- Match between `pipeline/graph.py:add_node("<name>", ...)` and `pipeline/contracts.py:NODE_INPUTS["<name>"]`.
- Filename: `pipeline/nodes/<node_name>.py`, function: `node_<node_name>(state)`.

**Vault entries:**
- Pattern: `YYYY-MM-DD-<2-to-4-word-kebab-slug>.md`
- Examples: `2026-04-13-passion-as-high-performance.md`, `2026-05-15-progress-visible-in-retrospect.md`
- Date matches `state["video_date"]`, NOT the run date. Slug derived from the entry's core insight (kebab-case, alphanumeric only, capped via `pipeline/runtime.py:slugify`).
- Multiple entries per day allowed when one video produces two distinct insights: `2026-03-17-growth-recognition-problem.md` + `2026-03-17-wanting-the-hard-choice.md`.
- Entries are immutable — kb_curator refuses overwrite of an existing entry path.

**Vault themes:**
- Pattern: `<single-or-multi-word-kebab-slug>.md` (no date prefix).
- Examples: `deliberate-discomfort.md`, `body-literacy.md`, `comfort-as-default.md`, `discipline-under-chaos.md`.
- Pre-approved themes (no HITL approval needed): existing themes already in the directory + `pattern-manifestation` (auto-attached to `Flagged` entries).
- Vault stays intentionally coarse (~11 active themes = book chapters); fine-grained classification lives in Notion (~49 sub-themes — see memory "Vault vs Notion themes"). Do NOT sync the two taxonomies.

**Vault frameworks:**
- Pattern: `<kebab-slug>.md`.
- Examples: `amcc-effect.md`, `cookie-jar-types.md`, `friction-types.md`, `phase-1-protocol.md`, `the-three-modes.md`.

**Run IDs:**
- Pattern: `YYYY-MM-DD_HHMMSS` (single video) or `YYYY-MM-DD_HHMMSS_NNN` (batch — always 3-digit suffixed so two videos in the same wall-clock second cannot collide; see `pipeline/run.py:_run_batch`).

**Run directories:**
- Pattern: `processed/<run_id>/<run_suffix>/` where `<run_suffix>` defaults to `langgraph`.
- Successful original video archived to `processed/<run_id>/source/`.

**Telemetry files:**
- Pattern: `<run_dir>/runs.jsonl` (per-stage rows), `reports/daily-summarizer-runs.jsonl` (daily-brief rows).
- Format: One JSON object per line; fields include `ts`, `stage`, `duration_s`, `model`, token counts, billing mode.

**Brief directories:**
- Pattern: `briefs/<theme-slug>/<YYYY-MM-DD>--<sub-angle-slug>/`.
- Always three files inside: `deep-dive.md`, `application.md`, `audio-prompts.md`.

**Transcript files:**
- Pattern: `transcript_YYYY-MM-DD.txt` (or `_<N>.txt` when one date has multiple recordings).
- Whisper output: `<video-parent-dir>/auto-generated/transcript_YYYY-MM-DD.txt`.

**Notion database "data source" IDs:**
- Stored in env (`NOTION_BLOG_DATA_SOURCE_ID`, `NOTION_RESEARCH_DATA_SOURCE_ID`) with hardcoded prod fallbacks in `pipeline/notion_client.py`.
- Distinct from the user-visible Notion "database" id — modern Notion API needs the `data_source_id` UUID for `create_pages` (see OPERATIONS.md §6 "Debugging" — `data_source_id should be a valid uuid`).

## Where to Add New Code

**New pipeline stage (LangGraph node):**
1. Create `pipeline/nodes/<name>.py` defining `def node_<name>(state: State) -> Dict[str, Any]` starting with `assert_inputs("<name>", state)`.
2. Register required inputs in `pipeline/contracts.py:NODE_INPUTS["<name>"] = [...]`.
3. Add state fields (if any) to `pipeline/state.py:State` (use `Annotated[List, add]` for fields appended in parallel branches).
4. Wire into `pipeline/graph.py:build_graph` — `g.add_node("<name>", node_<name>, retry_policy=_RETRY_POLICY)` (or omit retry policy if the node self-handles errors), then `g.add_edge(...)` to connect.
5. Add an audit check in `pipeline/nodes/validator.py:_audit` (core or secondary).

**New external service integration:**
- Add a thin wrapper module under `pipeline/<service>_client.py`.
- Keep all REST/SDK calls there — pipeline nodes should not import third-party SDKs directly.

**New scheduled job:**
- Add a `python -m pipeline.<thing>` entry point (CLI in `__main__.py`).
- Document in `OPERATIONS.md` §8.
- Add a systemd user unit at `~/.config/systemd/user/painforwisdom-<thing>.{service,timer}`.
- If failure mode could silently kill the timer, add a cron watchdog at `pipeline/scripts/check_<thing>_freshness.sh` mirroring `check_daily_brief_freshness.sh`.

**New agent prompt:**
- File: `.claude/agents/<name>.md` with YAML frontmatter + role description + `## OUTPUT` section.
- Load from a node via `pipeline/runtime.py:load_agent_prompt("<name>.md")` — frontmatter and `## OUTPUT` are stripped automatically; the node injects its own structured `_OUTPUT_SPEC`.

**New theme / framework:**
- Pipeline auto-creates these via kb_curator's HITL approval — do NOT hand-author files in `obsidian-vault/gonzalo-book/themes/` or `frameworks/` unless reseeding.
- For coarse taxonomy changes (umbrella splits, etc.), edit `data/themes.yaml` then run `python -m pipeline.scripts.seed_themes_db` + `python -m pipeline.scripts.render_curator_taxonomy --apply`.

**New test:**
- Unit tests: `tests/test_<module>.py`, pytest-style.
- Smoke fixtures: Add to `tests/fixtures/transcript_<date>-<descriptor>.txt`; update `tests/fixtures/README.md` matrix.

**New CLI helper / migration script:**
- Place under `pipeline/scripts/` if part of the pipeline package (importable, gets `python -m pipeline.scripts.<name>`).
- Place under `scripts/` at repo root for standalone operator tools (not packaged).

## Special Directories

**`obsidian-vault/`:**
- Purpose: Git submodule, NOT part of this repo's history. Pipeline writes commit to `gonandrap/painforwisdom-kb`.
- Generated: No (manual content + pipeline writes).
- Committed: Yes (in the submodule; this repo only tracks the submodule pointer).
- Branch: `draft` (pipeline writes land here); manual merge to `main` after review.

**`obsidian-vault-sandbox/`:**
- Purpose: Sandbox vault worktree (`git worktree add ../obsidian-vault-sandbox` from inside the submodule).
- Generated: No.
- Committed: gitignored at the parent level.

**`processed/`, `bulk/`, `briefs/`, `books/`, `reports/`, `to_be_retried/`, `data/temp/`:**
- Generated: Yes (pipeline outputs).
- Committed: No (gitignored).

**`pipeline/state/`, `pipeline/checkpoints.db`, `pipeline/checkpoints-sandbox.db`:**
- Generated: Yes (LangGraph checkpoint state, themes DB cache).
- Committed: No (gitignored).
- Rebuilds: `themes.db` auto-rebuilds on first access from `data/themes.yaml`; checkpoints persist across process restarts and are required for HITL resume — do not delete unless intentionally discarding pending interrupts.

**`.planning/`:**
- Purpose: GSD planning artifacts (codebase maps, phase plans). Tooling/agent scratch.
- Committed: Per project preference.

**`.claude/worktrees/`:**
- Purpose: Duplicate working copies for parallel Claude Code sessions.
- Committed: No.
- Important: Ignored by codebase mappers to avoid double-counting.

---

*Structure analysis: 2026-05-18*
