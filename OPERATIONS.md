# Operations Guide

Day-to-day commands for the LangGraph pipeline. For background and topology see [`README.md`](README.md).

All commands assume:

```bash
conda activate painforwisdom-poc
cd ~/workspace/painforwisdom/painforwisdom
```

---

## 1. Run the pipeline (production)

| Goal | Command |
|------|---------|
| Single video, full pipeline | `python -m pipeline.run --video bulk-daily/PXL_*.mp4` |
| Directory of videos (sequential batch) | `python -m pipeline.run --dir bulk-daily/` |
| Skip Whisper, feed a transcript | `python -m pipeline.run --from-transcript path/to/transcript_YYYY-MM-DD.txt` |

Useful flags:

| Flag | Effect |
|------|--------|
| `--profile prod` *(default)* | Reads `.env` |
| `--profile sandbox` | Reads `.env.sandbox`; tags Telegram with `[SANDBOX] ` |
| `--run-id <id>` | Override the auto timestamp run id |
| `--target-seconds 600` | Soft wall-clock target for the validator audit (default 10 min) |
| `--reminder-interval 1800` | How often (seconds) to re-post the HITL/error prompt while waiting (default 30 min, waits forever) |
| `--auto-approve` | Auto-reply `yes` to every HITL approval and abort fast on errors. **TEST ONLY.** |
| `--telegram-on-error` | Pairs with `--auto-approve`: posts the error prompt to Telegram non-blocking before re-raise. Used by the smoke harness. |

Batch mode automatically moves successful videos to `<dir>/processed/` and failed ones to `<dir>/quarantine/`, and posts a final summary to Telegram.

### What you'll see

- `📥 Pipeline intake — …` on every run start.
- `⚠️ NEW THEME — approval required` *(or framework)* if `kb_curator` proposes something new — reply `yes`, `no`, or an alternative slug.
- Per-stage progress lines in the terminal; `processed/<run_id>/<suffix>/` keeps everything on disk.
- Final `🎉 Pipeline PASS` / `⚠️ Pipeline PARTIAL` / `❌ Pipeline FAIL` summary.

---

## 2. Sandbox & smoke tests

The sandbox profile points the pipeline at duplicated Notion DBs and a parallel vault worktree so smoke runs never touch prod.

### One-time setup

1. Duplicate `Blog post pending publications` and `Research Tasks` databases in Notion. Share both with your integration.
2. Fetch their `data_source_id`s via the Notion API (the workflow in `tests/sandbox_reset.sh` shows the call shape) and paste into `.env.sandbox`.
3. Create the parallel vault worktree:
   ```bash
   git -C obsidian-vault worktree add ../obsidian-vault-sandbox
   ```
4. Copy `.env.sandbox.template` → `.env.sandbox`, fill in IDs.

### Run the smoke harness

```bash
# Reset the sandbox state first (revert vault worktree + archive Notion pages)
bash tests/sandbox_reset.sh

# Run the default fixture
bash tests/smoke_pipeline.sh

# Or run a specific fixture (see tests/fixtures/README.md for the matrix)
SMOKE_FIXTURE=tests/fixtures/transcript_2026-04-15-flagged.txt \
  bash tests/smoke_pipeline.sh
```

### Available fixtures

| Fixture | Quality | Pipeline branch |
|---------|---------|-----------------|
| `transcript_2026-04-14.txt` | Strong | `NEEDS_APPROVAL_THEME` HITL → `PROCEED` |
| `transcript_2026-04-15-flagged.txt` | Flagged | Auto-attaches `pattern-manifestation`, no HITL |
| `transcript_2026-04-16-weak.txt` | Weak | `PROCEED` (entry created, flagged for thinness) |
| `transcript_2026-04-17-strong-existing-themes.txt` | Strong | `PROCEED` directly — no HITL, fastest happy path |

See [`tests/fixtures/README.md`](tests/fixtures/README.md) for what each one exercises.

### Iteration loop

```bash
# In one session:
bash tests/sandbox_reset.sh && bash tests/smoke_pipeline.sh
# … fix bug …
bash tests/sandbox_reset.sh && bash tests/smoke_pipeline.sh
```

`sandbox_reset.sh` is idempotent and safe to run repeatedly.

---

## 3. Pre-flight: verify Notion + forecast cost

Before the first sandbox/prod run after a schema change or before a large batch:

```bash
# Verify NOTION_API_KEY works and target databases match the expected schema
python -m pipeline.smoke_notion

# Forecast tokens, $ cost, and quota share for a transcript / video
python -m pipeline.cost_forecast --transcript path/to/transcript.txt
python -m pipeline.cost_forecast --video path/to/video.mp4
```

Both are non-destructive — neither creates Notion pages nor calls the LLM.

---

## 4. Retry failed transcripts

Stage failures and weak content auto-copy the transcript to `to_be_retried/` and post a Telegram alert. To reprocess:

```bash
# Slash command in Claude Code or Gemini CLI
/retry-failed                   # process every file in to_be_retried/
/retry-failed transcript.txt    # process a single file
```

Or shell directly:

```bash
python -m pipeline.run --from-transcript to_be_retried/transcript_YYYY-MM-DD.txt
```

---

## 5. Extract a transcription from a video (no pipeline)

```bash
# Slash command
/extract-transcription path/to/video.mp4 [language] [YYYY-MM-DD]

# Or directly
bash extract_transcription.sh path/to/video.mp4 English 2026-04-13
```

---

## 6. Debugging

| Symptom | First check |
|---------|-------------|
| `Invalid authentication credentials` | OAuth token expired — `claude setup-token` and re-export `ANTHROPIC_AUTH_TOKEN`, or fall back to `ANTHROPIC_API_KEY`. |
| `data_source_id should be a valid uuid` | `.env` / `.env.sandbox` has the DB *name* instead of the data source UUID. Fetch via `GET /v1/databases/<id>` with `Notion-Version: 2025-09-03`. |
| `kb-curator: response missing ---kb-plan--- markers` | LLM dropped the structured output. The node retries once automatically; a double-flake means more retries needed in `pipeline/nodes/kb_curator.py:_call_llm_for_plan`. |
| `Submodule clone failed` | Vault submodule is private to a different account. Ensure your local git auth has access. |
| `entry already exists, refusing overwrite` | Run on the same date already wrote to the vault. Reset with `bash tests/sandbox_reset.sh` (sandbox) or remove the file in the vault submodule (prod) before re-running. |
| Telegram intake message missing | `telegram_io.sh` failed at startup — check `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`. The pipeline keeps running on Telegram failures (non-fatal). |

### Inspect a previous run

```bash
ls processed/<RUN_ID>/<suffix>/                # per-stage output dirs
cat processed/<RUN_ID>/<suffix>/runs.jsonl     # telemetry (one line per stage)
cat processed/<RUN_ID>/<suffix>/usage.jsonl    # live LLM usage (one line per call start, end or error)
cat processed/<RUN_ID>/<suffix>/validator/audit_report.md
```

### Resume a HITL run mid-flight

LangGraph SqliteSaver checkpoints persist across process restarts:

```bash
# Re-launch with the SAME --run-id; the graph resumes from the last checkpoint
python -m pipeline.run --from-transcript ... --run-id <existing_run_id>
```

The pending `interrupt()` asks again, in the console and on Telegram; answer in either.

---

## 7. Notifications quick reference

All pipeline events go to Telegram, and every question below also appears in the console (§9); the first answer from either wins. The pipeline pauses at:

- **Stage 3 approval** (`kb_curator`) — new theme/framework. Reply `yes`, `no`, or an alternative slug.
- **Error escalation** — after RetryPolicy is exhausted on a persistent (non-transient) error. Reply `retry` (re-invoke from checkpoint) or `abort`.

`--auto-approve` short-circuits both for tests; pair with `--telegram-on-error` to still get a non-blocking error notification on phone.

---

## 8. Scheduled jobs (systemd user units)

Background automations run via **systemd --user**. `crontab -l` is empty by design — do not look there.

| Unit | Schedule | What it does |
|------|----------|--------------|
| `painforwisdom-daily-brief.timer` → `painforwisdom-daily-brief.service` | Daily 06:00 local | `python -m pipeline.summarize_daily --apply --mcp-publish --max-cost-usd 1.0 --count 3` — picks up to 3 distinct-theme clusters from Notion Research queue → builds 3 briefs at `briefs/<theme>/<date>--<sub-slug>/` → uploads each to NotebookLM → posts 3 Telegram summaries (one per brief) to the `daily_summary` channel with a direct audio-overview link each. |

The `--count` flag (default 3) feeds Gonzalo's 2h commute + 1h run listening window. Each brief gets its own audio overview and its own Telegram message so they queue up in the dedicated `daily_summary` channel.

The Telegram message uses the **direct audio CDN URL** (`audio_url` field returned by `nlm studio status --full --json`) so the click-to-listen works on mobile without navigating NotebookLM's notebook → studio panel. If the audio is still rendering at poll timeout, the message falls back to the NotebookLM project URL plus a note to open the mobile app.

### Telegram channel routing

`TELEGRAM_DAILY_SUMMARY_CHAT_ID` in `.env` routes daily-summarizer messages to a dedicated channel (`daily_summary` = `-1003515954802`), so the main `content_pipeline` chat stays focused on per-transcript pipeline progress. If unset, daily messages fall back to `TELEGRAM_CHAT_ID`. The bot must be a member of the channel with post permission before this works.

Unit files: source in `pipeline/systemd/`, installed to `~/.config/systemd/user/` by `pipeline/scripts/install-pipeline-units.sh`. Both units run code from the tag-pinned `~/workspace/painforwisdom-live` (moved by a pipeline or coach ship) with `PAINFORWISDOM_STATE_ROOT` = dev folder, so `.env`, `books/`, `briefs/`, `reports/` and vault reads stay there.

### Inspect

```bash
# Confirm timer is armed and see last/next fire times
systemctl --user list-timers | grep painforwisdom

# Last exit status (Active=activating means restart loop in flight)
systemctl --user status painforwisdom-daily-brief.service --no-pager

# Tail logs (today, last 200 lines)
journalctl --user -u painforwisdom-daily-brief.service --since today -n 200 --no-pager

# Show unit file contents
systemctl --user cat painforwisdom-daily-brief.service painforwisdom-daily-brief.timer
```

### Manual run / reset

```bash
# Stop a restart loop (service has Restart=on-failure / RestartSec=300 / StartLimitBurst=3 / StartLimitIntervalSec=2h)
systemctl --user stop painforwisdom-daily-brief.service

# Fire it now as a one-shot (writes to journal, same as scheduled run)
systemctl --user start painforwisdom-daily-brief.service

# Disable / re-enable the daily timer
systemctl --user disable --now painforwisdom-daily-brief.timer
systemctl --user enable  --now painforwisdom-daily-brief.timer
```

### Symptom: "no daily Telegram brief / no new NotebookLM today"

| Check | Command | Meaning |
|-------|---------|---------|
| Did the timer fire? | `systemctl --user list-timers \| grep painforwisdom` | `LAST` column shows last fire — if it's today, the timer is fine. |
| Did the service crash? | `systemctl --user status painforwisdom-daily-brief.service` | `Active: activating (auto-restart)` or `failed` = crash. |
| Why did it crash? | `journalctl --user -u painforwisdom-daily-brief.service --since today` | Look for `FetchError`, `NotionError`, traceback. |

Common crash: `FetchError: html http-status 403` from `pipeline/summarize_daily/fetcher.py:_fetch_html`. The brief writer now catches per-row fetch failures and continues with survivors — you get a Telegram alert listing the skipped URLs and the brief still publishes (unless all sources fail, in which case the run aborts with an `❌ Daily brief ABORTED` Telegram message).

**Ban a flaky URL** so the picker never selects it again: append the exact URL on its own line to [`config/fetch_denylist.txt`](config/fetch_denylist.txt). The file is re-read on every run — no restart needed. Denylisted rows are dropped in `clusterer.fetch_pending_rows`.

Other escape hatches:

```bash
# (a) Identify the picked cluster + URLs without spending a cent
python -m pipeline.summarize_daily --dry-run

# (b) Test which URL is the 403 outside the pipeline
python -c "import httpx; print(httpx.get('<url>', headers={'User-Agent':'Mozilla/5.0 painforwisdom-summarizer/4'}, follow_redirects=True, timeout=15).status_code)"

# (c) Re-run skipping that theme so the brief still ships
/home/gonzalo/miniconda3/envs/painforwisdom-poc/bin/python -m pipeline.summarize_daily \
  --apply --mcp-publish --max-cost-usd 1.0 --skip-themes <bad-theme>
```

After unblocking, re-arm and re-fire:

```bash
systemctl --user reset-failed painforwisdom-daily-brief.service
systemctl --user start        painforwisdom-daily-brief.service
```

---

## 9. Pipeline console

Local web UI to start runs, watch the DAG live, inspect each step's facts, findings and artifacts (skipped steps are amber) and open a run's output.
Design: `docs/superpowers/specs/2026-10-31-pipeline-console-ui.md`.

```bash
# Sandbox (default profile). No env to export: runs load .env.sandbox themselves, and the
# server reads only NOTION_API_KEY from .env.sandbox for the result view.
python -m pipeline.ui --profile sandbox --inbox /path/to/inbox
# open http://127.0.0.1:8820
```

- Header shows running version next to logo: nearest git tag of the checkout (`pipeline-vX.Y.Z` or coach `vX.Y.Z`), read at server start.
- Host is always `127.0.0.1`; port `--port` / `PIPELINE_UI_PORT` (default `8820`).
- Inbox: `--inbox` / `PIPELINE_UI_INBOX` (default `$STATE_ROOT/inbox`). Drop `.txt` transcripts or videos
  (`.mp4 .mov .mkv .webm .m4v .avi`) there; they appear in the play-button popup.
- Runs write to `--processed-root` / `PIPELINE_UI_PROCESSED_ROOT` (default `processed/` of the checkout the
  server runs from); the UI reads the same root.
- Prod is never the default: `--profile prod` (or `PIPELINE_UI_PROFILE=prod`) must be chosen explicitly; it reads `.env`.
- Operate everything here; Telegram is a notification and a second answer channel. Decision map: `pipeline/ui/e2e/decision_tree.json`, design: `docs/superpowers/specs/2026-11-05-console-end-to-end.md`.
- Dashboard (`#/`, the home page): needs you, tiles, recent runs (open, delete) and the latest themes. `Start a new run` is in the header on every page.
- Needs you: every run wanting a decision; one click opens it. Dismiss offers Undo for 8 s; `Dismissed (n)` restores later.
- Delete a run: removes `processed/<id>/` and its checkpoint rows, so its input no longer warns "Already ran". The input, vault entry, Notion pages and anything published stay. Refused while it's active, publishing or converting a preview, and when it has `source/`, which can be the only copy of its input. A re-run can still stop at a curator question, because the vault entry stays.
- Dry run (the switch in the Start popup, or `python -m pipeline.run --dry-run`): the whole pipeline on the real input at the same AI cost, keeping nothing outside `processed/<id>/`. The vault is a scratch copy, Notion goes to the tables in `.env.sandbox` (it refuses to start without them), nothing can be published, and the same video can repeat. `tests/sandbox_reset.sh` clears the sandbox Notion pages. Design: `docs/superpowers/specs/2026-11-06-pipeline-dry-run-design.md`.
- Run page (`#/runs/<id>`): totals, graph and output cards; each opens its details. A finished run can restart from its input.
- Knowledge base (`#/knowledge`): entries and theme counts read from `<vault>/gonzalo-book/entries`. The vault is `PIPELINE_UI_VAULT`, else `VAULT_PATH` in the profile's env file, else the default.
- Action banner: approvals (approve, reject, free text), errors (retry, abort), failures with node, reason and blockers; stop, retry from checkpoint, restart, dismiss. A run started by an older release can only be stopped, then retried.
- The popup's auto-approve toggle passes `--auto-approve`, which gives up after 10 approvals in one run.
- YouTube publish: **Publish on** (local date and time) schedules it: the upload goes up private with `publishAt` and YouTube makes it public then; the card and the panel say "Scheduled". Empty publishes on click with the chosen privacy. A past time is refused before anything uploads. Packages made before this field have no Publish on.
- YouTube settings the API cannot set: comments follow the channel's Upload defaults (Studio, Settings, Upload defaults, Advanced: allow comments; uploads go up "not made for kids", which keeps comments possible), and the video location is ignored when sent, so set it in Studio.
- Publishing shows a moving bar with what it is doing and for how long, until the package is published or fails.
- Costs are API-equivalent (`≈`) under subscription billing, from `config/pricing.json`. Update it when prices or models change; an unknown model shows `–`.
- One active run at a time (a second launch returns 409 and shows the live run).
- Views: Overview (Ctrl or ⌘ + wheel zooms the graph, drag pans, `+ - 0`), Output and Logs, as tabs and in the route `#/runs/<id>/<view>`. The inspector docks beside the graph.
- Output: one section at a time (`j`/`k`): summary, blog, YouTube, WordPress, knowledge base, research, audit, files. Text files edit in place. A package can be prepared for an older run, saved without publishing, and published; the result or error shows in place. Local books open from the research table, served only from `$STATE_ROOT/books`.
- Video preview: when the browser cannot decode a package's video (HEVC from a phone), the console converts a copy with ffmpeg (H.264, long side 1280, HDR tone-mapped), about half the video's length on this machine, and swaps it in. A run starts that conversion itself as soon as its YouTube package exists, for any video that isn't H.264, in a process of its own that the run does not wait for; opened before it is done, the package shows an animation until the copy plays. It is cached at `processed/<run>/console-preview/`, safe to delete, and never uploaded: the upload always sends the original.
- Metrics bar (bottom, one line): tokens, cache, cost, time for the run or the selected node. `Details` opens the per-node table.
- The server only answers `Host` 127.0.0.1, localhost or [::1], and refuses cross-origin writes.

### Prod console service

Prod runs as `painforwisdom-console.service` (systemd `--user`, `--profile prod`, code from `~/workspace/painforwisdom-live`, state from the dev folder). Runs and their history live in the live checkout's `processed/`.

**Ship the pipeline:** `pipeline/scripts/shipit.sh [patch|minor|major|X.Y.Z] [--base <40-char sha>] [--dry-run]`. Tags `pipeline-vX.Y.Z` on an `origin/main` commit (no release branch, nothing bumped), pushes it, publishes a GitHub release, deploys, notifies Telegram. Never builds, restarts or health-checks the coach. After a failed deploy, rerun `pipeline/scripts/deploy-live.sh <tag>`, not the ship; it refuses a tag not reachable from `origin/main`.

Both lines deploy the console, because both move the same checkout: `deploy-live.sh` (pipeline, or coach after its health check) runs `pipeline/scripts/install-pipeline-units.sh --no-restart` from the new tag, restarts the service and waits up to 30 s for `GET http://127.0.0.1:8820/api/graph` to return 200. A coach ship therefore also moves pipeline code. A pipeline ship moves the checkout the coach watchdog composes from, while coach images stay as built.

Deploys refuse with `pipeline run <run_id> active; retry after it ends` while a run in `<live>/processed/` is active with a live pid, because agent prompts are read from disk at node time. Retry the same `deploy-live.sh <tag>` after it ends. `DEPLOY_IGNORE_ACTIVE_RUN=1` overrides. A restart alone never kills a run (`KillMode=process`).

**Before the first install, stop the hand-started console.** It holds port 8820, so the service could not bind and the deploy's health check would pass against the old process. Its runs are detached, so killing the server does not kill them:

```bash
pgrep -af 'pipeline\.ui'    # find the hand-started server's pid
kill <pid>
```

```bash
systemctl --user status painforwisdom-console.service --no-pager
journalctl --user -u painforwisdom-console.service -n 200 --no-pager
pipeline/scripts/install-pipeline-units.sh --dry-run   # print the plan and rendered unit, touch nothing
```

The installer takes `--dry-run`, `--unit-dir <dir>` (writes there, skips systemctl) and `--no-restart`, plus the path overrides `PFW_LIVE_DIR`, `PFW_STATE_ROOT`, `PFW_PYTHON`. A rollback to a tag older than this unit keeps the installed unit; if that tag lacks the installer or `pipeline/ui`, the console step fails, with the coach already up on that tag.

### Publishing

```
extract ─▶ extract_image ─────────────────────────┐
writer ─┬─▶ notion_blog ──────────────────────────┴─▶ wordpress_draft ─▶ bv_wordpress ─▶ summarizer
        └─▶ youtube_metadata ─▶ youtube_upload ─▶ bv_youtube ───────────────────────────▶ summarizer
```

`config/publishing.json` (committed, no secrets) sets each target's `mode`, per profile. Credentials stay in the profile env file (`.env` / `.env.sandbox` under `$STATE_ROOT`).

| `mode` | The node writes | DAG |
|---|---|---|
| `off` (sandbox default) | bundle only | skipped |
| `manual` (prod default) | bundle and `package.json`, status `pending` | skipped |
| `auto` | bundle and `package.json`, then publishes in-process | done |

- A package lives at `processed/<run>/langgraph/{wordpress-draft,youtube-upload}/package.json`.
- `GET /api/runs/<run>/publish` lists a run's packages; `POST /api/runs/<run>/publish/<target>` with `{"fields": {...}}` publishes one. The server never loads secrets: it spawns the CLI, which reads the profile env file itself.
- By hand: `python -m pipeline.publish --profile prod --run-dir processed/<run>/langgraph --target wordpress`. Output goes to `publish.log` next to the package when the console spawns it.
- `publish.lock` next to the package marks a publish in progress. The console clears it, and marks the package `failed`, when its publisher died.
- WordPress.com's free plan blocks writes: a publish on it ends `failed` with a 403 in `error`.
- YouTube needs a video: a transcript run skips both YouTube nodes with reason `no video`. `youtube_metadata` writes `youtube-upload/metadata.json`; an empty title after one retry leaves a `warning` there and in the package, and the title must be filled before publishing.
- YouTube `kind`: `short` appends `#Shorts` to the description and fails over 180 s; `video` uploads as is.
- Language: the Start-a-run popup has a code field, prefilled from `language` in `config/publishing.json` (`en`). Whisper transcribes with it instead of detecting, and the run keeps it for retry and restart. A code Whisper does not know fails the run at `transcribe`; the reason is in the run's `stdout.log`.
- YouTube recording date and location start from the video's own tags (local day; location rounded to about 1 km, and editable or clearable before publishing). Both languages start from the language chosen for the run. Caption certification has no API field: set it in Studio. `publish.log` prints what YouTube stored; it may ignore `location`, which its API has marked deprecated since 2017.

Tests (from `pipeline/ui/e2e/`):

```bash
npm ci && npx playwright install chromium
npm test        # fake runner + fixtures, whole suite, 100% browser coverage gate
# What CI runs: `console browser (i/4)` shards, then `console coverage gate` merges their raw coverage.
# Locally, one shard after another (parallel runs in one checkout collide in test-results/):
rm -rf coverage-shards
npm run test:shard -- --shard=1/2   # raw coverage to coverage-shards/1-of-2, no gate
npm run test:shard -- --shard=2/2
npm run coverage:merge              # needs every shard of the split; throws below 100%
# Real sandbox runs through the UI (transcript, then video). Minutes per run; never in CI.
# Needs .env.sandbox at the repo root; resets the sandbox before each run.
PYTHON=/home/gonzalo/miniconda3/envs/painforwisdom-poc/bin/python \
  PFW_REAL_VIDEO=/path/to/short.mp4 PFW_SHOTS=/tmp/shots npm run test:real
```

---

## 10. Pipeline v2 (sessions) and A/B harness

v2 runs the same stages and prompts as the LangGraph pipeline, with LLM calls as Claude Agent SDK sessions (spec `docs/superpowers/specs/2026-11-09-session-pipeline-v2.md`). Same flags as `pipeline.run`, without `--dir`. Sessions use the Claude Code login, not `ANTHROPIC_API_KEY`.

```bash
python -m pipeline.v2.run --from-transcript tests/fixtures/transcript_2026-04-14.txt --profile sandbox --dry-run --auto-approve
```

Until bl-dff0b22f lands, prefix a direct v2 run with `ANTHROPIC_AUTH_TOKEN=` or every session gets 401. From a worktree, set `PAINFORWISDOM_STATE_ROOT` to the main checkout so `.env.sandbox` is found.

A/B: both engines on one transcript, `--dry-run --auto-approve`, one after the other (never in parallel), run ids `ab-<ts>-<n>-v1` and `ab-<ts>-<n>-v2`. Sandbox only.

```bash
python -m pipeline.v2.ab --from-transcript tests/fixtures/transcript_2026-04-14.txt --repeat 2 --profile sandbox
```

Report: `processed/ab/<ts>/report.md` and `report.json`, child logs beside them. Per engine: wall time, node durations, LLM calls, tokens by kind, retries, errors, verdict; outputs side by side; validator findings that differ. It compares, it does not rate quality. A run that `--auto-approve` refuses (kb-curator asking a real decision, e.g. the 2026-04-17 fixture's `HALT_DUPLICATE_SUSPECTED`) shows as `refused by --auto-approve: True`. Compare tokens on `prompt_total`: v1 `input_tokens` already counts cache tokens, v2's does not.

Audit DB (SQLite, one row per run, step, session, approval and artifact):

| Run | DB |
|-----|----|
| `--dry-run` | `processed/<run_id>/dry-run/v2.db` |
| `--profile sandbox` | `pipeline/v2-sandbox.db` |
| prod | `pipeline/v2.db` |

`PIPELINE_V2_DB` overrides the non-dry-run path.

```bash
python -m pipeline.v2.audit <run_id> --db processed/<run_id>/dry-run/v2.db     # steps, sessions, approvals, artifacts
python -m pipeline.v2.audit --db <db> --session <n>                           # session row #n in full: prompt and output
```

---

## 11. File / directory reference

| Path | Notes |
|------|-------|
| `pipeline/run.py` | CLI entry point (`python -m pipeline.run`) |
| `pipeline/graph.py` | DAG topology, `RetryPolicy`, checkpointer |
| `pipeline/nodes/*.py` | Per-stage logic — start here when something breaks |
| `pipeline/llm.py` | LiteLLM wrapper, OAuth refresh, retry on 401/429 |
| `pipeline/notion_client.py` | Notion REST helpers |
| `pipeline/ui/` | Pipeline console server + static UI; browser tests in `pipeline/ui/e2e/` |
| `tests/smoke_pipeline.sh` | Sandbox driver |
| `tests/sandbox_reset.sh` | Vault revert + Notion archive |
| `tests/fixtures/` | Reusable transcripts |
| `pipeline/agents/*.md` | Stage prompts (loaded by `pipeline/runtime.py:load_agent_prompt`) |
| `processed/<run_id>/` | Per-run outputs (gitignored) |
| `pipeline/checkpoints*.db` | LangGraph checkpoint state (gitignored) |
