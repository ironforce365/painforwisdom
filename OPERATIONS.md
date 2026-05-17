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
cat processed/<RUN_ID>/<suffix>/validator/audit_report.md
```

### Resume a HITL run mid-flight

LangGraph SqliteSaver checkpoints persist across process restarts:

```bash
# Re-launch with the SAME --run-id; the graph resumes from the last checkpoint
python -m pipeline.run --from-transcript ... --run-id <existing_run_id>
```

The pending `interrupt()` re-prompts on Telegram so you can reply with the approval.

---

## 7. Notifications quick reference

All pipeline events go to Telegram. The pipeline pauses indefinitely at:

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

Unit files: `~/.config/systemd/user/painforwisdom-daily-brief.{service,timer}`.

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

## 9. File / directory reference

| Path | Notes |
|------|-------|
| `pipeline/run.py` | CLI entry point (`python -m pipeline.run`) |
| `pipeline/graph.py` | DAG topology, `RetryPolicy`, checkpointer |
| `pipeline/nodes/*.py` | Per-stage logic — start here when something breaks |
| `pipeline/llm.py` | LiteLLM wrapper, OAuth refresh, retry on 401/429 |
| `pipeline/notion_client.py` | Notion REST helpers |
| `tests/smoke_pipeline.sh` | Sandbox driver |
| `tests/sandbox_reset.sh` | Vault revert + Notion archive |
| `tests/fixtures/` | Reusable transcripts |
| `.claude/agents/*.md` | Stage prompts (loaded by `pipeline/runtime.py:load_agent_prompt`) |
| `processed/<run_id>/` | Per-run outputs (gitignored) |
| `pipeline/checkpoints*.db` | LangGraph checkpoint state (gitignored) |
