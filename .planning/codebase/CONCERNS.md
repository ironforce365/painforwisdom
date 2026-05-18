# Codebase Concerns

**Analysis Date:** 2026-05-18
**Scope:** `pipeline/` (LangGraph DAG + daily summarizer), `obsidian-vault/` submodule, shell tooling at repo root. `.claude/worktrees/` ignored to avoid double-counting.
**Severity legend:** `[high]` immediate user-visible/data-integrity risk · `[med]` reliability / cost / maintainability · `[low]` cleanup, polish.

---

## Tech Debt

### `[high]` Orphaned agent prompt — `blog-post-catchy-title.md`

- Files: `.claude/agents/blog-post-catchy-title.md` (120 lines)
- Issue: User memory states this stage was dropped (titles never came out good). The prompt file still exists on disk but `grep -r "blog-post-catchy-title|catchy" pipeline/` returns zero hits — no LangGraph node loads it.
- Impact: Recurring confusion. A future planner or contributor will see the file and re-wire it. Dead prompts also bloat anyone listing `.claude/agents/` to map stages.
- Fix approach: Delete the file. If retention is needed for history, move it to a `attic/` directory clearly labeled "not loaded by pipeline."

### `[med]` AnythingLLM backend is a stub, configured to be selectable via env

- Files: `pipeline/blog_context/anythingllm_backend.py:40`, `pipeline/blog_context/anythingllm_backend.py:50`
- Issue: Two `TODO(blog_context):` comments. Both methods `raise NotImplementedError`. Selection happens by `BLOG_CONTEXT_BACKEND=anythingllm` env flip.
- Impact: A live env edit flips the writer node from working ("vault" backend) to broken (NotImplementedError) silently — only the writer call site surfaces it.
- Fix approach: Either delete the stub until the AnythingLLM workspace is real, or fail loud at backend registration time (`get_backend("anythingllm")` should raise immediately, not at first method call inside a graph node).

### `[med]` LangChain pending-deprecation warning silenced at package import

- Files: `pipeline/__init__.py:1-23`
- Issue: A `LangChainPendingDeprecationWarning` is filtered globally before any LangGraph code runs (LC_REVIVER `allowed_objects` default). Comment notes "we cannot pass through to it."
- Impact: When LangChain ships the breaking change, the pipeline will deserialize SqliteSaver checkpoints with the new default and may fail to revive interrupted runs. Silenced warnings rot — when it matters, no one will see it coming.
- Fix approach: Track upstream LangChain release notes; pin `langchain-core` to the pre-change version in `pipeline/requirements.txt` or implement the `allowed_objects` plumbing before the warning becomes an error.

### `[low]` Hardcoded fallback Notion data-source IDs

- Files: `pipeline/notion_client.py:32-37`
- Issue: Production Notion data source UUIDs are baked into source as `_DEFAULT_BLOG_DATA_SOURCE_ID` / `_DEFAULT_RESEARCH_DATA_SOURCE_ID`. Env overrides exist but defaults are committed to git.
- Impact: Sandbox runs that forget to set the override write to prod DBs. A fork of the repo by another user inherits Gonzalo's DB pointers (writes will 401 only because the integration key won't match — the safety is implicit).
- Fix approach: Drop the fallbacks; raise at startup when `NOTION_BLOG_DATA_SOURCE_ID` / `NOTION_RESEARCH_DATA_SOURCE_ID` are unset.

### `[low]` Schema-tolerant Notion writes paper over missing migrations

- Files: `pipeline/notion_client.py:90-114`, `pipeline/notion_client.py:146-178`
- Issue: `create_blog_page` / `update_blog_page_wordpress_url` silently drop `Status`, `Excerpt`, `WordPress URL` properties when the blog DB schema hasn't been migrated. They `print` a WARN but don't fail.
- Impact: Run looks PASS but data is silently incomplete. Easy to miss in batch summary.
- Fix approach: Promote those WARNs to a validator finding (already partly there for blog body emptiness) so PARTIAL verdict catches them.

---

## Known Bugs / Fragility

### `[high]` Vault submodule is dirty in working tree

- Files: `obsidian-vault/` submodule (committed pointer `521791024148529ae9e1f0ed57f0013916a8bc38`)
- Symptoms: `git status` shows `m obsidian-vault`. Inside the submodule:
  ```
   M .obsidian/graph.json
   M gonzalo-book/_index.md
   M gonzalo-book/book-outline.md
   M gonzalo-book/themes/incremental-exposure.md
  ?? gonzalo-book/entries/2026-05-15-progress-visible-in-retrospect.md
  ?? gonzalo-book/themes/incremental-progress.md
  ```
- Trigger: `kb_curator` writes vault files in `pipeline/nodes/kb_curator.py:_apply_proceed` (entry, themes, frameworks, `_index.md`, `book-outline.md`) but never `git add` / `git commit` inside the submodule. Every pipeline run leaves uncommitted vault state.
- Impact: A `git submodule update --remote --rebase` from CI/setup would discard pending entries. New themes (`incremental-progress`) and entries (`2026-05-15-progress-visible-in-retrospect`) are at risk of being thrown away on a careless reset.
- Workaround: Manually `cd obsidian-vault && git add -A && git commit && git push`. Bump submodule pointer in main repo afterward.
- Fix approach: Either teach `kb_curator` (or a post-node hook) to commit+push the vault submodule, or add a watchdog that pages Telegram when the submodule has uncommitted entries older than N hours. Confirm against user memory note `feedback_no_silent_feature_drops.md` before changing commit behavior.

### `[high]` `extract_transcription.sh` `set -euo pipefail` + multi-backend selection

- Files: `extract_transcription.sh:5-30`, `pipeline/nodes/transcribe.py:36-80`
- Issue: Shell script uses strict mode and supports `WHISPER_BACKEND=local|openai|auto`. Transcribe node only detects CUDA OOM via regex; other transient Whisper failures (audio decode error, missing model file, model corruption) bubble as `RuntimeError("extract_transcription.sh exit <rc>")` with no actionable detail.
- Impact: Pipeline quarantines the video and Telegram surfaces only the exit code. Retrying via `pipeline.retry` will hit the same shell failure indefinitely.
- Fix approach: Pass stderr through to the error-recovery prompt (`pipeline/run.py:_format_error_prompt` already truncates at 800 chars but the upstream exception only carries the rc). Capture proc.stderr tail into the RuntimeError message.

### `[med]` Telegram delivery silently degrades to plain text

- Files: `telegram_io.sh:41-59`, `pipeline/telegram.py:30-65`
- Issue: `_send()` in shell only checks `ok != "True"` and prints `WARNING` to stderr. The Python wrapper returns `proc.returncode` but most callers either ignore it (`return rc != 0`) or only log it non-fatally. HTML parse-mode failures (unescaped `<`/`>`/`&` in dynamic content) return 400 from Telegram — the run does not fail, but the user gets no notification.
- Impact: A run can PASS with no Telegram alert because the message contained an unescaped `<theme-name>`. Fixed locally in `pipeline/summarize_daily/__main__.py:_esc()` but every other call site that uses `parse_mode="HTML"` must remember to escape interpolated values.
- Fix approach: Move `_esc` to `pipeline/telegram.py` and require all callers to pass already-escaped text when `parse_mode="HTML"`, OR add a Python-side parse_mode-aware escaper inside `pipeline.telegram.send`.

### `[med]` `telegram_io.sh` `wait_reply` polling loop has no timeout cap from shell

- Files: `telegram_io.sh:71-102`
- Issue: `_wait_reply()` loops forever polling Telegram every 5s. Timeout enforcement lives in the Python caller via `subprocess.run(..., timeout=timeout_seconds)`. Anyone invoking the script directly (e.g. an ops/debug script) gets no timeout.
- Impact: Forgotten shell-level invocations hang indefinitely, consuming Telegram getUpdates quota.
- Fix approach: Honour a `$TELEGRAM_WAIT_TIMEOUT` env var (or argv) inside the shell function; fail with rc=124 (POSIX timeout convention) when exceeded.

### `[med]` `wait_reply` consumes whichever first text update arrives, no chat-id filter

- Files: `telegram_io.sh:82-93`
- Issue: The Python parser inside the loop iterates `data['result']` and prints the FIRST text. It does not filter by chat_id or sender. A bot in multiple chats (e.g. `daily_summary` and `content_pipeline`) will accept a reply from either.
- Impact: HITL approvals routed to the pipeline chat can be unintentionally satisfied by an unrelated message in the daily-summary channel (or vice-versa once watchdog notices land in `daily_summary`).
- Fix approach: Filter by `msg.chat.id == CHAT_ID` before returning. Currently the inline Python snippet doesn't check.

### `[med]` Daily-brief Telegram audio CDN URL was broken; mitigation drops the URL entirely

- Files: `pipeline/summarize_daily/__main__.py:280-300` (Listen link), commit `fd1468b`
- Issue: NotebookLM `audio_url` is a Google CDN URL requiring Sign-In; not shareable. Current code drops it from the Telegram message and only links the NotebookLM project URL via `<a href=...>Listen</a>`.
- Impact: User must have the NotebookLM mobile app installed for the click-through to work. Desktop click lands on the notebook page, requires navigating to studio panel manually.
- Workaround in place: Comment in `__main__.py` explicitly notes the trade-off.
- Fix approach: Audio overview hosted somewhere durable (own CDN) — needs design work. Acceptable as-is per user memory note `feedback_audio_overview_format.md` (mobile listening is the canonical path).

### `[med]` `notion-blog` page-id extraction is URL-string hackery

- Files: `pipeline/nodes/validator.py:188-194`
- Issue: `page_id = str(bu).rstrip("/").split("-")[-1].replace(":", "").replace("/", "")` — extracts the page UUID from the notion.so URL by string surgery.
- Impact: Notion URL format change breaks the verification. Validator falls through to the `except Exception` and the audit marks "Notion blog body non-empty" as missed.
- Fix approach: Store `notion_blog_page_id` from `pipeline/nodes/notion_blog.py` (`state["notion_blog_page_id"]` is already in `State` per `pipeline/state.py:62`) and read that in the validator instead of URL parsing.

---

## Performance Bottlenecks

### `[high]` Pipeline wall-clock baseline ~30 min/transcript (Paperclip era)

- Files: User memory `pipeline_perf_baseline.md`; targets: full-migration <10 min, PoC <5 min on canonical video `PXL_20260413_194231193.mp4`
- Problem: No per-stage timing target enforced anywhere. `--target-seconds 600` flag in `pipeline/run.py:435` is reported but not enforced.
- Impact: A user-facing 6× perf regression target is tracked in user memory but invisible in the repo. No regression alert when a node creeps slower.
- Improvement path:
  1. Add a per-stage budget table (transcribe ≤ X s, extract ≤ Y s, …) in `pipeline/graph.py` or a config file.
  2. Validator emits a PARTIAL when stage duration > 2× budget (telemetry already collected via `append_metric` → `processed/<run_id>/<suffix>/runs.jsonl`).
  3. Cost forecast (`pipeline/cost_forecast.py`) currently estimates tokens — extend to wall-clock predictions per stage.

### `[high]` Whisper CPU fallback is ~10× slower than GPU

- Files: `pipeline/nodes/transcribe.py:62-74`, `extract_transcription.sh:WHISPER_DEVICE`
- Problem: CUDA OOM on contended GPU triggers automatic CPU re-run. A single ~15-min video on CPU + `medium` model ≈ 25-40 min.
- Impact: One OOM blows the <10 min target. User is notified but the run still completes — wall-clock balloons silently from the orchestrator's perspective.
- Improvement path: Telegram alert is sent, but the validator does NOT capture "this run used CPU fallback" as a PARTIAL finding. Add a state flag (`whisper_device_fallback: str`) so a PARTIAL verdict surfaces the slow path.

### `[med]` `kb_curator` re-prompts the LLM in a loop on every HITL turn

- Files: `pipeline/nodes/kb_curator.py:406-432`
- Problem: Each `NEEDS_APPROVAL` round = full `_call_llm_for_plan(state, approval_history)` re-invocation. The system prompt + vault snapshot + extraction report are re-sent every turn. Cache-control on the system block helps, but the user message (with PRIOR APPROVAL EXCHANGE growing) is not cached and grows unbounded.
- Impact: Multi-turn refinement (user iterates 3-4 times on a theme name) = 3-4 full input bills. Tokens grow linearly. The recent commit `19e0e6e` bounded error-recovery prompts but the kb-curator approval-history loop was not touched.
- Improvement path: Cap `approval_history` to last N turns (3) with a summary placeholder for older turns, or move the history into a structured field that downstream re-invocations can diff against.

### `[med]` `kb_curator` retry-on-format-error doubles tokens silently

- Files: `pipeline/nodes/kb_curator.py:280-300`
- Problem: When the LLM forgets the `---kb-plan---` markers OR emits invalid YAML, the node retries once with a corrective prompt. The retry sums tokens into the first metric entry (`result2[k] = result[k] + result2[k]`) — visible in telemetry but only if you inspect `processed/<run_id>/.../runs.jsonl`. No Telegram surface, no validator finding.
- Impact: Token quota drain on a flaky model run is invisible to the operator.
- Improvement path: Bubble a `kb_curator_format_retries` counter to state and let the validator render it.

---

## LLM Cost / Quota Concerns

### `[high]` 1M-context beta header gating — recent fix

- Files: `pipeline/llm.py:153-167` (commit `19e0e6e`)
- Issue: The `context-1m-2025-08-07` beta header used to be sent on every subscription call. Without the long-context billing add-on, Anthropic returns `rate_limit_error: "Extra usage is required for long context requests"` even on tiny prompts. Fixed by gating on a per-call `long_context: bool` flag.
- Remaining risk: Every caller of `call_llm` must explicitly opt in. No call site currently sets `long_context=True` (`grep -rn "long_context" pipeline/`). If a future caller forgets the flag on a >200k input, the request will fail rather than degrade. The `_classify_non_recoverable` path in `pipeline/run.py:_classify_non_recoverable` correctly identifies the misleading rate-limit message — keep it.
- Fix approach: Add a guardrail in `call_llm` — when input token estimate > 180k and `long_context=False`, log a warning so the caller knows they're about to hit the cap.

### `[high]` Error-recovery prompt growth — recent fix

- Files: `pipeline/run.py:_format_error_prompt:117-130`, `pipeline/run.py:_ask_bounded` (commit `19e0e6e`)
- Issue: Past behavior accumulated unbounded prompts in retry loops. Now `_format_error_prompt` truncates exception messages at 800 chars; `_ask_bounded` caps reminder re-posts at 5 (~2.5h on 30-min interval).
- Remaining risk: The `pipeline/retry.py` resume-driver uses `_ask_indefinitely` (`pipeline/retry.py:213`) for error recovery — NOT `_ask_bounded`. Quarantined-video retries can still wait forever on a Telegram reply.
- Fix approach: Mirror the bounded-reminder change into `pipeline/retry.py:_resume_graph` so retry runs also have a max wait window. Otherwise a forgotten-about quarantine retry holds the systemd unit open indefinitely.

### `[med]` Token-rotation re-reads credentials.json on every call

- Files: `pipeline/llm.py:40-54`, `pipeline/token_rotation.py`
- Issue: Every `call_llm` invocation re-reads `~/.claude/.credentials.json` from disk. Cheap (small JSON) but the auth-error retry path also re-reads — meaning every call after a 401 reads twice.
- Impact: Minor — disk I/O per call. No security risk (no logging of the token). Worth noting only because it complicates testing: mocking auth requires intercepting `read_oauth_token`.
- Fix approach: Acceptable as-is; the comment in `llm.py:39-44` justifies the cost.

### `[med]` No quota forecast before daily-brief multi-count runs

- Files: `pipeline/summarize_daily/__main__.py:--count` flag (default 3)
- Issue: Each brief = ~$0.40 / 1 LLM run (synthesis + per-row summaries + application + audio prompts). With `--count 3` daily on subscription, that's ~12 brief-units of quota per day across morning/commute/run windows.
- Impact: Subscription has no $ but does have request-per-5h and ITPM caps. A heavy day (long sources, retries on format errors) can push the daily timer toward the cap before 06:00 finishes.
- Per user memory `feedback_cost_forecast_before_replay.md`: project tokens + cost + quota share before any batch replay. Daily-brief is a recurring "batch" — same rule should apply.
- Fix approach: `pipeline.cost_forecast --daily-brief` mode that projects N × per-brief tokens and reports quota share against `PRO_MAX5_SONNET_MSGS_PER_5H` (already defined in `pipeline/cost_forecast.py:30`). Run it on systemd-timer pre-start.

### `[low]` 1M-context billing add-on disabled is detected via error-message string match

- Files: `pipeline/run.py:_classify_non_recoverable:90-103`
- Issue: Non-recoverable classifier scans `str(exc).lower()` for `"extra usage is required for long context requests"`. Brittle to Anthropic error-message wording changes.
- Impact: A reword would silently re-enable the futile retry loop the classifier was designed to prevent.
- Fix approach: Match on Anthropic's structured error code instead (`error.type == "rate_limit_error"` + body inspection) once LiteLLM exposes the underlying response.

---

## Notion API Rate-Limit Pacing

### `[med]` `_PACE_SECONDS = 0.4` is conservative but uniform

- Files: `pipeline/notion_client.py:38` (constant), `pipeline/summarize_daily/notion_state.py:18` (separate `NOTION_PACING_S = 0.5`)
- Issue: Two different pacing constants in two files; neither uses an adaptive backoff. Hard-coded 0.4s post-call sleep on every Notion request.
- Impact: A burst of e.g. 6 research-task pages = ~2.4s minimum wall-clock. A daily-brief run that touches 3 clusters × 3 rows = 9 page updates × 0.5s = 4.5s baseline before any retry. If Notion's actual limit moves (rare but possible), we have no signal — `_PACE_SECONDS` is a magic number.
- Fix approach:
  1. Centralise pacing in `pipeline.notion_client` and import from `summarize_daily.notion_state`.
  2. Use `tenacity` or similar to retry on 429 with exponential backoff and read the `Retry-After` header.

### `[low]` Notion schema cache never invalidates within a run

- Files: `pipeline/notion_client.py:_BLOG_SCHEMA_CACHE:43-44`
- Issue: `get_blog_db_properties()` caches the schema once per process; `force_refresh=True` is never called anywhere.
- Impact: A schema migration run mid-process (unlikely) would miss new properties for the rest of the run.
- Fix approach: Acceptable. Document the cache scope.

---

## Watchdog Reliability

### `[med]` Daily-brief watchdog runs from cron, depends on user-systemd env at runtime

- Files: `pipeline/scripts/check_daily_brief_freshness.sh:50-80`
- Recent fix: heal-then-notify on dead timer (commit `2e21bd9`). Cron now restarts the timer before paging.
- Remaining risk:
  1. `XDG_RUNTIME_DIR=/run/user/$(id -u)` and `DBUS_SESSION_BUS_ADDRESS` are set inside the script — works only when `loginctl enable-linger gonzalo` has been applied. Not enforced anywhere; if linger flips off, all `systemctl --user` calls fail with `Failed to connect to bus`.
  2. Dedupe state file (`data/.daily_brief_watchdog.last_alert`) lives in repo working dir. A `git clean -fdx` wipes it; the next run re-pages.
  3. Alert routing depends on sourcing `.env` from cron context (no inherited env). If `.env` is missing or unreadable by the cron user, alerts go to nowhere — script still exits 0.

- Fix approach:
  1. Document `loginctl enable-linger` requirement in `OPERATIONS.md`; add a self-test that fails loudly when linger is off.
  2. Move dedupe state to `~/.local/state/painforwisdom/` outside the repo.
  3. Add a final-line "alert sender confirmed" log line; if both chat-id env vars are empty, fail with rc=1.

### `[med]` Retry budget for daily-brief = 3 starts in 2h (recent OPERATIONS.md update, commit `57d5cf5`)

- Files: `OPERATIONS.md:179-189`, systemd unit `painforwisdom-daily-brief.service` (referenced, not in repo: `~/.config/systemd/user/`)
- Issue: `StartLimitBurst=3 StartLimitIntervalSec=2h` means a sustained outage (Notion 5xx for >2h) will permanently hold the service in `failed` until manual reset. Watchdog will then heal the timer, but if the service unit is in a `start-limit-hit` state the timer-triggered start is rejected too.
- Impact: A multi-hour upstream outage → user-visible "no brief today" without recovery until `systemctl --user reset-failed` is run by hand.
- Mitigation in place: Watchdog calls `systemctl --user reset-failed` before `start` (`pipeline/scripts/check_daily_brief_freshness.sh:67`). Good. But the watchdog runs from cron at its own cadence — there's a gap between failure and heal.
- Fix approach: Verify the watchdog cron cadence is at most every 2h. Document it. (`crontab -l` is empty per `OPERATIONS.md:159` — the actual cron is owned by root or systemd-cron; cadence not visible in repo.)

### `[low]` Watchdog has no self-test path

- Files: `pipeline/scripts/check_daily_brief_freshness.sh`
- Issue: No way to simulate "no brief in 25h" without faking mtimes. No unit test or fixture.
- Impact: A typo in the alert message goes unnoticed until a real outage.
- Fix approach: Add a `--dry-run-stale` flag that prints the would-send alert text without touching state files.

---

## Telegram Delivery Fragility

### `[high]` Recent commits cluster around Telegram reliability — pattern signals systemic risk

Commits in the last 30 push events touching Telegram:
- `19e0e6e` LLM beta gating + error-recovery prompt bounds (#34)
- `6cdbdbf` caller env overrides .env; watchdog alerts to daily-summary channel (#31)
- `fd1468b` HTML href Listen link, drop broken audio CDN URL, fail-loud on focal pre-summary loss (#30)
- `ba605f1` Fix silent Telegram drops and simplify .gitignore (#6)
- `13a2736` Surface weak content reason in extraction report and Telegram notification

This is 5+ commits in the active span. The shape suggests Telegram delivery is the canonical "thing that almost works" surface.

### `[med]` Multi-channel routing depends on caller env override

- Files: `telegram_io.sh:18-30`
- Issue: `_caller_*` variables snapshot caller-supplied env BEFORE `.env` sourcing, then re-apply after — so caller env wins (recent fix). Subtle precedence: `TELEGRAM_DAILY_SUMMARY_CHAT_ID` is not handled here; the override is done in Python (`pipeline.summarize_daily.__main__:_daily_chat_id`) by passing `TELEGRAM_CHAT_ID=<daily-channel-id>` as env to the subprocess.
- Impact: A new caller that sets `TELEGRAM_DAILY_SUMMARY_CHAT_ID` and expects the shell to honor it will get the default `TELEGRAM_CHAT_ID` from `.env`. The two-layer routing (shell vs Python) is non-obvious.
- Fix approach: Document the routing in `pipeline/telegram.py` docstring. Consider moving channel-selection logic entirely into Python so the shell only ever knows `TELEGRAM_CHAT_ID`.

### `[med]` `_send` Telegram failures are non-fatal everywhere

- Files: throughout: `pipeline/run.py:354 ("telegram intake notify rc=... non-fatal")`, `pipeline/run.py:434-437` (retry on rc!=0 once), `pipeline/nodes/transcribe.py:65-70` (try/except Exception around send)
- Issue: A bot-token rotation or chat-id change causes ALL pipeline runs to silently lose every notification. The validator records "Telegram delivered" as a secondary finding — but if Telegram is broken, the validator's own delivery also fails (chicken-and-egg).
- Impact: User loses the only feedback loop.
- Fix approach: At pipeline `run.py:main()` start, do a single ping to Telegram with the run intake message; if it fails twice, exit early with a clear log line so the operator catches the auth break before the pipeline burns LLM tokens.

---

## Vault Sync Concerns

### `[high]` See "Known Bugs" above — submodule dirty in `git status`

Re-listed here for category completeness. The vault uses a git submodule (`gonandrap/painforwisdom-kb`); the pipeline writes vault files but never commits. Recovery path requires manual `cd obsidian-vault && git add … && git commit && git push` + bumping the submodule pointer in the main repo.

### `[med]` Vault path is env-overridable; pipeline writes mass files at `VAULT_PATH/gonzalo-book/`

- Files: `pipeline/runtime.py:20-22`, `pipeline/nodes/kb_curator.py:34` (`VAULT_ROOT = VAULT_PATH / "gonzalo-book"`)
- Issue: If `VAULT_PATH` env points at a non-git directory (e.g. sandbox), `kb_curator` still mass-writes; only the submodule path is durable. Sandbox profile uses `obsidian-vault-sandbox` (a separate worktree).
- Impact: Misconfigured env can write entries to a non-tracked dir; pipeline reports PASS while data is unrecoverable.
- Fix approach: Validator should confirm `VAULT_ROOT/.git` exists (or the worktree marker) before accepting a PASS.

### `[low]` Vault sub-theme drift vs Notion themes

- Per user memory `feedback_vault_vs_notion_themes.md`: vault stays coarse (~11 themes = book chapters), Notion stays fine (~49 sub-themes = search index); explicit "do NOT sync."
- Files: `pipeline/themes_db.py` resolves sub-theme → parent umbrella for agent routing in `pipeline/nodes/notion_research.py:_resolve_agent`. Code already respects the separation.
- Impact: None today; flagging for future planners who might be tempted to "unify" them.

---

## Sensitive Material

### `[high]` `.env` and `.env.sandbox` contain secrets and are gitignored

- Files: `.env`, `.env.sandbox`, `.env.sandbox.template`
- Content (per `.gitignore:4-7` + README): API keys (`ANTHROPIC_API_KEY` or `ANTHROPIC_AUTH_TOKEN`), `TELEGRAM_BOT_TOKEN`, `OPENAI_API_KEY`, `NOTION_API_KEY`, `WORDPRESS_USERNAME` / `WORDPRESS_APP_PASSWORD`, Notion DB UUIDs, NotebookLM auth (via `nlm` CLI profile), Z-Library credentials.
- Risk: Properly gitignored — confirmed `.env` lines 4-6 of `.gitignore`. `.env.sandbox.template` is committed and is a safe template (no real values).
- Concerns:
  1. `.env.sandbox.swp` is gitignored (`.gitignore:7`) — implies someone edited it with vim and a swap file leaked at some point. Verify no `.swp` is committed in history.
  2. Hardcoded production Notion data-source UUIDs in `pipeline/notion_client.py:32-37` — not secret, but ties a forked repo to Gonzalo's databases until env is overridden.
  3. `ZLIBRARY_EMAIL` / `ZLIBRARY_PASSWORD` (per `pipeline/zlibrary_bridge.py:10-14`) are credentials to a content-piracy service — operational risk if the host is shared.
- Fix approach: `git log --all -p -- .env* | grep -i "API_KEY\|TOKEN\|PASSWORD"` to confirm no historical leak. (Out of scope for this audit; flag for verification.)

### `[med]` OAuth credentials.json read from disk every LLM call

- Files: `pipeline/llm.py:48-54`, `pipeline/token_rotation.py`
- Path: `~/.claude/.credentials.json` (managed by the `claude` CLI)
- Risk: A misconfigured host with relaxed home-dir perms exposes the OAuth token. Pipeline assumes filesystem-level isolation.
- Fix approach: Document required perms (0600) on `~/.claude/.credentials.json` in README setup section.

---

## Coupling: LangGraph nodes ↔ `.claude/agents/*.md`

### `[med]` Prompts are versioned outside Python code, loaded at runtime

- Files: `pipeline/runtime.py:load_agent_prompt:60-78`, agent prompts in `.claude/agents/*.md`
- Coupling:
  - `pipeline/nodes/extract.py` → `coaching-thought-extractor.md` (236 lines)
  - `pipeline/nodes/kb_curator.py` → `kb-curator.md` (365 lines)
  - `pipeline/nodes/writer.py` → `painforwisdom-writer.md` (210 lines)
  - `pipeline/nodes/research.py` → `research-curator.md` (371 lines)
  - `pipeline/nodes/youtube_upload.py` → `youtube-upload-agent.md` (70 lines)
  - `pipeline/nodes/validator.py` → `pipeline-summary.md` (90 lines) — actually unused (validator is pure-Python)
- Issue:
  1. Prompts are markdown files, read with `path.read_text()`, frontmatter stripped, OUTPUT section stripped, then `CACHE_PADDING_APPENDIX` appended. Any edit to the prompt → silent behaviour change on next run. No version pinning, no checksum.
  2. The OUTPUT-section strip in `load_agent_prompt` (`pipeline/runtime.py:75`) uses regex `r"\n##\s+OUTPUT\s*\n"` — if a prompt author renames `## OUTPUT` to `## Output Spec` the strip fails and the model gets the legacy Bash-based output instructions, which conflict with the appended `EXECUTION CONTEXT` rules.
  3. Cache-floor coupling: `CACHE_TOKEN_FLOOR = 2200` (comment "empirically ~2,048 tokens"). `CACHE_PADDING_APPENDIX` exists primarily to push prompts over this floor. If a prompt shrinks below the floor, caching silently disengages — token costs rise without warning.
- Impact: A prompt-only edit (no code change) can break a node. No CI signal.
- Fix approach:
  1. Add a `pipeline/scripts/check_agent_prompts.py` that validates each prompt:
     - Frontmatter parseable
     - `## OUTPUT` section heading exact
     - Resulting body + appendix ≥ `CACHE_TOKEN_FLOOR` tokens (use litellm token counter)
  2. Wire it into a `make check` target.
  3. Track prompt versions: stamp each prompt with a `version:` frontmatter key and log it into `runs.jsonl` so a regression can be tied to a prompt change.

### `[low]` `validator.py` does not load `pipeline-summary.md`

- Files: `.claude/agents/pipeline-summary.md`, `pipeline/nodes/validator.py`
- Issue: validator is pure-Python (`pipeline/nodes/validator.py:1-21`); the agent file appears dead.
- Impact: Same as `blog-post-catchy-title.md` — confusion vector.
- Fix approach: Delete or annotate as historical reference.

---

## Test Coverage Gaps

### `[high]` 8 of 11 pipeline nodes have zero tests

Nodes with NO `test_*` file:
- `pipeline/nodes/transcribe.py`
- `pipeline/nodes/kb_curator.py` (most complex node: HITL, YAML, file I/O across submodule)
- `pipeline/nodes/notion_blog.py`
- `pipeline/nodes/notion_research.py`
- `pipeline/nodes/validator.py`
- `pipeline/nodes/writer.py`
- `pipeline/nodes/wordpress_draft.py`
- `pipeline/nodes/youtube_upload.py`
- `pipeline/nodes/extract_image.py`

Nodes with tests:
- `pipeline/nodes/extract.py` — covered indirectly via 3 fixture transcripts in `tests/fixtures/`
- `pipeline/nodes/research.py` — `tests/test_research_node.py` (12 test functions)

Risk:
- `kb_curator` writes to the vault submodule with `entry_path.write_text(...)` and refuses overwrite via runtime check (`pipeline/nodes/kb_curator.py:314-317`). No test verifies overwrite refusal, YAML block-scalar handling, or multi-turn approval-history collapse.
- `validator` decides PASS/PARTIAL/FAIL — the core run verdict. No test verifies that a missing `vault_entry_path` produces FAIL (it should, per `validator.py:69-71`).
- `notion_research` paces at 0.4s per row, but no test verifies pacing or row-content building.

Fix approach:
1. Prioritise `kb_curator` and `validator` — both have the largest blast radius.
2. Use the existing `tests/fixtures/` transcripts to drive end-to-end smoke (already done by `tests/smoke_pipeline.sh`) but the unit-test coverage is sparse.
3. Per user memory `feedback_poc_before_migration.md` — any refactor of these nodes >2 days needs a slim PoC first; tests buy that.

### `[med]` `summarize_daily` clusterer + brief_writer have no unit tests

Files with no tests:
- `pipeline/summarize_daily/clusterer.py` (theme picking logic)
- `pipeline/summarize_daily/brief_writer.py` (cluster → v2-PoC adapter)
- `pipeline/summarize_daily/notebooklm_publisher.py` (subprocess wrapper around `nlm` CLI)
- `pipeline/summarize_daily/notion_state.py` (Notion writebacks)

Only `tests/test_summarize_daily_fetch_resilience.py` (6 tests) covers the fetcher.

Risk: The daily-brief is on a systemd timer running unattended. A clusterer regression silently picks wrong themes; a publisher regression silently skips NotebookLM upload.

Fix approach: Unit tests for `clusterer.pick_cluster` (deterministic — pure Python over Notion-row dicts) are cheap. Add a fixture set of synthetic rows and validate theme priority + dedupe.

### `[low]` `pipeline.retry` module has no tests

- Files: `pipeline/retry.py` (409 lines — largest pipeline module after `run.py`)
- Coverage: zero unit tests; `tests/sandbox_reset.sh` exercises it implicitly.
- Risk: Resume-from-checkpoint semantics are subtle; a regression here corrupts checkpoint state.

---

## Silent Feature Drops — User Has Explicitly Warned Against These

Per user memory `feedback_no_silent_feature_drops.md`: when a planned tech choice blocks implementation, surface for explicit decision. Never silently substitute.

Audit of observed drops in the codebase:

### `[med]` `WORDPRESS_ENABLED!=true` defaults pipeline to dormant mode

- Files: `pipeline/nodes/wordpress_draft.py:11-15`, `pipeline/wordpress_client.py:13-16`
- Behavior: WordPress free plan blocks writes; pipeline silently writes a "dormant bundle" to disk instead of calling the API.
- Surface: Stage logs `[wordpress-draft] start` and state field `wordpress_dormant=True`. Validator does NOT raise PARTIAL on dormant mode. User must check `wordpress-draft/*.json` manually.
- Concern: This is the silent-drop pattern the memory warns about — a "drop" that's documented but not surfaced run-by-run.
- Fix approach: Emit a one-line PARTIAL finding when `wordpress_dormant=True`, OR include the dormant state in the validator Telegram summary.

### `[med]` `YOUTUBE_ENABLED!=true` similarly dormant

- Files: `pipeline/nodes/youtube_upload.py:8-15`
- Same pattern: writes metadata JSON to disk for manual upload, returns `youtube_skipped=True`.
- Fix approach: Same as WordPress — surface a PARTIAL finding.

### `[low]` `image_extraction_failed` is swallowed if OpenCV / ffmpeg missing

- Files: `pipeline/nodes/extract_image.py:1-19`
- Behavior: Module-level docstring explicitly states "Failures inside the extractor … are caught and reported via `image_extraction_failed=True`; the WordPress node must tolerate a missing image."
- Concern: Marked as `True` in state but validator does not check this field (`pipeline/nodes/validator.py` audit list).
- Fix approach: Add a validator finding (`severity="secondary"`) reading `image_extraction_failed`.

---

## Fragile Areas

### `[high]` `kb_curator` YAML parsing — three retry layers, all string-shaped

- Files: `pipeline/nodes/kb_curator.py:236-296`
- Why fragile:
  1. Regex `_PLAN_PATTERN = re.compile(r"---kb-plan---\s*\n(.*?)\n---kb-plan---", re.DOTALL)` — model can emit nested or escaped markers and break the match.
  2. `yaml.safe_load` on free-text fields — block scalars (`>-`) required to avoid colons-in-prose breaking parsing. The prompt tells the model to use `>-` for `reason`, `core_tension`, `definition`, `curator_summary`. If the model forgets, the retry prompt explains block scalars — but if the model insists, the run quarantines.
  3. Output is then `path.write_text(plan["entry_body"])` — direct write, no schema validation. A malformed `entry_body` writes a broken markdown file to the vault.
- Safe modification: Add a `pydantic` model for the plan; validate before writing. Reject and retry on schema mismatch.
- Test coverage: None.

### `[med]` Pipeline graph branches fan-out from `extract` to 3 parallel nodes

- Files: `pipeline/graph.py:103-118`
- Topology: `extract` → `kb_curator`, `extract_image`, `youtube_upload` (all parallel). `wordpress_draft` joins on `notion_blog` + `extract_image`. `validator` joins on `wordpress_draft` + `notion_research` + `youtube_upload`.
- Why fragile: LangGraph's join semantics depend on disjoint state keys. State (`pipeline/state.py`) uses `TypedDict total=False` with `Annotated[List, add]` only for `metrics`. Other parallel writes (e.g. two nodes both setting `image_extraction_failed`) would clobber.
- Mitigation in place: Branches write to distinct state keys (`featured_image_path` for extract_image, `youtube_url` for youtube_upload, `vault_entry_path` for kb_curator).
- Safe modification: Anyone adding a new parallel branch MUST add new state keys; cannot extend existing ones. Document this in `pipeline/state.py`.

### `[med]` `litellm` version sensitivity — auth pattern changed between 1.55 and 1.83

- Files: `pipeline/llm.py:1-19` (docstring)
- Why fragile: Comment explicitly notes "LiteLLM 1.83+ accepts `ANTHROPIC_AUTH_TOKEN` directly via env var. Earlier versions (e.g. 1.55 used in the PoC) required passing a pre-built Anthropic client via `client=`. We dropped that pattern: in 1.83 it caused 401s …"
- Requirements: `pipeline/requirements.txt:3` pins `litellm>=1.55.0,<2`. The range spans the auth-pattern change.
- Impact: A fresh `pip install` could land on a 1.55–1.83 version that requires the dropped pattern.
- Fix approach: Tighten the pin to `litellm>=1.83,<2`.

### `[med]` `notebooklm_publisher` shells out to `nlm` CLI

- Files: `pipeline/summarize_daily/notebooklm_publisher.py:50-65`
- Why fragile: Parses stdout with regex (`_SOURCE_ID_RE`, `_ARTIFACT_ID_RE`, `_NB_ID_RE`) — any change in CLI output format breaks publishing. `nlm` is third-party (NotebookLM unofficial CLI); upgrades are not gated.
- Safe modification: Pin the `nlm` version in setup docs (not visible in `pipeline/requirements.txt` because it's a separate binary). Cache the expected output format with a smoke test.

---

## Scaling Limits

### `[med]` SqliteSaver checkpoint DB grows unbounded

- Files: `pipeline/checkpoints.db` (1.7 MB at audit time)
- Issue: Every run writes a checkpoint per node. No vacuum, no retention. Sandbox uses a separate file (`pipeline/checkpoints-sandbox.db`, 360 KB).
- Limit: SQLite handles many GB but checkpoint reads at the start of a retry could slow down as the DB grows. Long-term storage of completed runs.
- Improvement path: Periodic `pipeline.scripts.gc_checkpoints` that deletes threads whose state shows terminal `validator_verdict ∈ {PASS, PARTIAL}` older than N days.

### `[low]` Notion data-source ID is process-wide (single-tenant)

- Files: `pipeline/notion_client.py:33-37`
- Limit: One blog DB + one research DB per process. Can't operate against two prod DBs in the same run.
- Acceptable today (single-user pipeline).

---

## Dependencies at Risk

### `[med]` `langgraph-checkpoint-sqlite` pinned to `>=2.0,<3`

- Files: `pipeline/requirements.txt:2`
- Risk: SqliteSaver API has churned across LangGraph versions. Checkpoint DB format is internal; upgrade may require migration.
- Migration plan: Before bumping, dry-run `graph.get_state()` against existing `pipeline/checkpoints.db` to verify schema compatibility.

### `[med]` `trafilatura` for HTML extraction — quality varies per site

- Files: `pipeline/summarize_daily/fetcher.py:34`, `pipeline/nodes/research.py:23-27`
- Risk: Trafilatura returns `None` or <500 chars for paywalled / JS-rendered sites. The fetcher catches this and surfaces "all-rows-failed" to Telegram, but partial-quality extracts (200 chars of nav text) pass the minimum check and degrade brief quality silently.
- Improvement path: Quality-gate extracted text (boilerplate detection, language detection) before caching.

### `[low]` `pypdf` and `pdftotext` are both optional, both fallback paths

- Files: `pipeline/summarize_daily/fetcher.py:18-22, 45-50`
- Risk: A host missing both `poppler-utils` (system) and `pypdf` (Python) fails PDF extraction with `FetchError`. README does not document the system dep.
- Fix approach: Add `apt-get install poppler-utils` to README setup section.

---

## Missing Critical Features

### `[med]` No automated vault commit hook

- Per "Known Bugs" — `kb_curator` writes vault files but pipeline never commits the submodule. Listed here because it's the most-impact missing feature.
- Blocks: Reliable vault snapshot recovery after a host failure.

### `[low]` No `tests/` for `pipeline.cost_forecast`

- Files: `pipeline/cost_forecast.py` (300 lines)
- Blocks: Trusting the per-replay quota projections that user memory `feedback_cost_forecast_before_replay.md` mandates running before batch operations.

---

## Summary — Top 5 to Fix Next

1. `[high]` Add automated vault-submodule commit (or watchdog Telegram alert on dirty vault). Currently leaves uncommitted entries on every run.
2. `[high]` Add `tests/test_kb_curator.py` + `tests/test_validator.py`. These are the two highest-blast-radius nodes with zero coverage.
3. `[high]` Delete `.claude/agents/blog-post-catchy-title.md` and `.claude/agents/pipeline-summary.md` (dead prompts that mislead future planners; explicitly confirmed against user memory `feedback_drop_catchy_title.md`).
4. `[med]` Mirror `_ask_bounded` reminder budget into `pipeline/retry.py:_resume_graph` (currently still uses `_ask_indefinitely`).
5. `[med]` Surface dormant WordPress + YouTube modes as validator PARTIAL findings — they are currently silent feature-drops at run time.

---

*Concerns audit: 2026-05-18*
