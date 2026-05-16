# painforwisdom

Automates blog posts for [painforwisdom.wordpress.com](https://painforwisdom.wordpress.com) from raw video transcripts and builds a structured Obsidian knowledge base that serves as the foundation for Gonzalo's book.

The pipeline is a **LangGraph DAG** orchestrated as plain Python (`pipeline/`). It transcribes a video with Whisper, runs seven content stages against Anthropic Claude (Sonnet 4.6 by default, via the OAuth subscription path with API-key fallback), writes vault entries to a git submodule, posts blog posts and research tasks to Notion, and sends a Telegram summary.

For day-to-day commands see **[`OPERATIONS.md`](OPERATIONS.md)**.

## Repositories

| Repo | Purpose |
|------|---------|
| `gonandrap/painforwisdom` (this repo) | Pipeline code: LangGraph nodes, agent prompts, smoke harness |
| `gonandrap/painforwisdom-kb` | Obsidian vault — entries, themes, frameworks, research |

The vault lives as a git submodule at `obsidian-vault/`. Pipeline writes (new entries, theme updates, research) commit directly to `painforwisdom-kb`, keeping vault history separate from pipeline code.

---

## Setup

```bash
git clone --recurse-submodules https://github.com/gonandrap/painforwisdom.git
cd painforwisdom

# Conda env for the pipeline (Python 3.11)
conda create -n painforwisdom-poc python=3.11 -y
conda activate painforwisdom-poc
pip install -r pipeline/requirements.txt

# Subscription billing (preferred — $0 marginal on Pro/Max)
claude setup-token   # exports ANTHROPIC_AUTH_TOKEN

# OR API credits
# put ANTHROPIC_API_KEY=sk-ant-... in .env
```

`.env` (prod profile) must define:

| Var | Purpose |
|-----|---------|
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | Notifications + HITL approvals |
| `OPENAI_API_KEY` | Whisper transcription (Stage 1) |
| `NOTION_API_KEY` | Notion REST integration token |
| `NOTION_BLOG_DATA_SOURCE_ID` | Optional override; defaults to prod blog DB |
| `NOTION_RESEARCH_DATA_SOURCE_ID` | Optional override; defaults to prod research DB |
| `ANTHROPIC_AUTH_TOKEN` *or* `ANTHROPIC_API_KEY` | Anthropic auth |

Sandbox profile (`.env.sandbox`) points at duplicated Notion DBs and a parallel vault worktree — see [`.env.sandbox.template`](.env.sandbox.template).

---

## Pipeline topology

```
START → transcribe → extract → kb_curator
                                   ├─▶ writer ──▶ notion_blog ──┐
                                   └─▶ research ▶ notion_research ┤
                                                                  ├─▶ validator → END
```

Per-node retry policy classifies transient infra errors (network, 5xx) as retryable; persistent errors escalate to Telegram with `retry / abort` (see `pipeline/run.py:_drive_graph`). Checkpointing via SqliteSaver makes HITL `interrupt()` resume safe across restarts.

| Stage | Module | Output |
|-------|--------|--------|
| 1 | `nodes/transcribe.py` | `auto-generated/transcript_YYYY-MM-DD.txt` (Whisper) |
| 2 | `nodes/extract.py`    | `extraction_report.md` — core insight + quality gate |
| 3 | `nodes/kb_curator.py` | vault entry + theme/framework updates in `painforwisdom-kb` |
| 4a | `nodes/writer.py`    | `blog_post.md` |
| 4b | `nodes/research.py`  | `research_report.csv` |
| 5a | `nodes/notion_blog.py`     | Notion page in "Blog post pending publications" |
| 5b | `nodes/notion_research.py` | Notion tasks in "Research Tasks" database |
| 6 | `nodes/validator.py` | `audit_report.md` + Telegram summary; verdict PASS/PARTIAL/FAIL |

Each run produces a directory under `processed/<RUN_ID>/<run_suffix>/` with all stage outputs and a `runs.jsonl` telemetry trace.

---

## Quick start

```bash
# Full pipeline on a single video
python -m pipeline.run --video bulk-daily/PXL_20260413_194231193.mp4

# Bulk: a directory of videos (sequential, with quarantine on failure)
python -m pipeline.run --dir bulk-daily/

# Test mode: skip Whisper, feed a transcript directly
python -m pipeline.run --from-transcript path/to/transcript.txt --auto-approve
```

For the full command reference (sandbox, smoke, retries, debugging) see **[`OPERATIONS.md`](OPERATIONS.md)**.

---

## Branch workflow

```bash
git checkout main && git pull --recurse-submodules
git checkout -b my-feature
git submodule update --init
# … edits …
git push origin my-feature
gh pr create --base main --head my-feature
```

### Vault submodule branches

| Branch | Purpose |
|--------|---------|
| `draft` | Pipeline writes here — all new entries land on this branch |
| `main` | Stable vault — merge from draft after review |

The submodule in this repo is pinned to `draft` so pipeline output is immediately visible in Obsidian without a manual merge.

---

## Notifications

All pipeline events go to Telegram:

- **Run intake** — every video / transcript fed in posts a 📥 message with `run_id` + profile.
- **Stage 3 approval** — new theme or framework: reply `yes`, `no`, or an alternative slug. Pipeline waits forever (re-posting the prompt every `--reminder-interval` seconds).
- **Persistent error** — escalated as `retry / abort`. Reply `retry` to re-invoke from the last checkpoint, anything else to abort.
- **Run summary** — verdict (PASS/PARTIAL/FAIL) + per-stage audit findings.

Sandbox runs are auto-prefixed with `[SANDBOX] ` so prod chat stays clean.

---

## Scheduled jobs

Background automations run via **systemd user units** (not crontab — `crontab -l` will be empty).

| Unit | Schedule | Purpose |
|------|----------|---------|
| `painforwisdom-daily-brief.timer` → `.service` | Every day 06:00 local | Runs `python -m pipeline.summarize_daily --apply --mcp-publish --max-cost-usd 1.0 --count 3` — picks up to 3 distinct-theme clusters from Notion's Research queue, builds 3 briefs, publishes each to NotebookLM, posts one Telegram message per brief (with a direct audio-overview link) to the dedicated `daily_summary` channel. |

Unit files: `~/.config/systemd/user/painforwisdom-daily-brief.{service,timer}`.

Quick ops (full guide in [`OPERATIONS.md`](OPERATIONS.md#8-scheduled-jobs-systemd-user-units)):

```bash
systemctl --user list-timers                                 # see what's scheduled + last/next fire
systemctl --user status painforwisdom-daily-brief.service    # last exit status
journalctl --user -u painforwisdom-daily-brief.service -n 100  # tail logs
systemctl --user stop painforwisdom-daily-brief.service      # break a restart loop
systemctl --user start painforwisdom-daily-brief.service     # manual one-shot run
```

If the morning Telegram brief or NotebookLM upload is missing, this is the first thing to check.

---

## Project structure

```
painforwisdom/
├── .claude/                   # Claude Code agent prompts (loaded by pipeline) + skills
├── obsidian-vault/            # Git submodule → gonandrap/painforwisdom-kb (draft branch)
├── obsidian-vault-sandbox/    # Sandbox vault worktree (gitignored)
├── pipeline/                  # LangGraph pipeline (nodes, graph, runtime, telemetry)
├── tests/                     # Smoke harness + sandbox reset + transcript fixtures
├── processed/                 # Pipeline run outputs (gitignored)
├── to_be_retried/             # Failed transcripts queued for retry (gitignored)
├── extract_transcription.sh   # Whisper wrapper used by Stage 1
├── telegram_io.sh             # Telegram send/receive helpers
├── OPERATIONS.md              # Day-to-day commands
└── README.md                  # This file
```
