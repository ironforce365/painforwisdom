# painforwisdom

Automates the creation of blog posts for [painforwisdom.wordpress.com](https://painforwisdom.wordpress.com) from raw video transcripts recorded during runs, and builds a structured Obsidian knowledge base that serves as the foundation for Gonzalo's book.

## Repositories

| Repo | Purpose |
|------|---------|
| `gonandrap/painforwisdom` (this repo) | Pipeline code: agents, scripts, CLAUDE.md orchestration |
| `gonandrap/painforwisdom-kb` | Obsidian vault — all entries, themes, frameworks, research |

The vault lives as a **git submodule** at `obsidian-vault/`. Pipeline writes (new entries, theme updates, research) commit directly to `painforwisdom-kb`. This keeps vault history cleanly separated from pipeline code.

---

## Setup

```bash
# Clone with submodule
git clone --recurse-submodules https://github.com/gonandrap/painforwisdom.git

# If you already cloned without --recurse-submodules
git submodule update --init
```

Copy `.env.example` to `.env` and fill in your credentials:

```bash
cp .env.example .env
```

Required environment variables:
- `TELEGRAM_BOT_TOKEN` — Telegram bot token for pipeline notifications
- `TELEGRAM_CHAT_ID` — chat ID to send/receive messages
- `OPENAI_API_KEY` — used by Whisper for transcription

---

## Running the pipeline

### Full pipeline (KB + blog post)

```bash
./run-pipeline.sh path/to/transcript_YYYY-MM-DD.txt
```

Or trigger via Claude Code:
```
run the content pipeline on this transcript [paste transcript]
```

### KB only (no blog post)

```
run the knowledge base pipeline on this transcript, no blog post needed
Video date: YYYY-MM-DD [paste transcript]
```

### Bulk ingestion

```bash
./run-pipeline.sh path/to/transcripts/   # directory of transcript_YYYY-MM-DD.txt files
```

### Extract transcription from video

```
/extract-transcription path/to/video.mp4 [language] [YYYY-MM-DD]
```

---

## Pipeline stages

| Stage | Agent | Output |
|-------|-------|--------|
| 1 | `coaching-thought-extractor` | `extraction_report.md` — core insight, quality gate |
| 2 | `kb-curator` | vault entry + theme updates in `painforwisdom-kb` |
| 3 | `painforwisdom-writer` | `blog_post.md` (Strong quality only) |
| 4 | `notion-blog-post-logger` | Notion page in "Blog post pending publications" |
| 5 | `blog-post-catchy-title` | 2–3 title candidates appended to Notion page |
| 6 | `research-curator` | `research_report.csv` + vault research section |
| 7 | `notion-research-logger` | Notion tasks in "Research Tasks" database |

Each run produces a directory under `processed/<RUN_ID>/<transcript_name>/` with all stage outputs.

### Failed or weak files

If a transcript fails a stage or produces weak content, it is automatically copied to `to_be_retried/` and a Telegram alert is sent. To reprocess:

```
/retry-failed                  # process all pending files
/retry-failed transcript.txt   # process one specific file
```

---

## Branch workflow

```bash
# Start a new task
git checkout main && git pull --recurse-submodules
git checkout -b my-feature

# The submodule is inherited — initialize if needed
git submodule update --init

# Work, commit pipeline code changes here
git add .claude/agents/my-agent.md CLAUDE.md
git commit -m "Add my agent"

# Vault changes go to painforwisdom-kb automatically (pipeline commits there directly)

# Open PR to main when ready
git push origin my-feature
gh pr create --base main --head my-feature
```

### Vault submodule branches

| Branch | Purpose |
|--------|---------|
| `draft` | Pipeline writes here — all new entries land on this branch |
| `main` | Stable vault — merge from draft after review |

The submodule in this repo is pinned to the `draft` branch so pipeline output is immediately available in Obsidian without a manual merge.

---

## Notifications

All pipeline events are sent to Telegram. The pipeline pauses and waits for a reply at:
- **Stage 1 flag gate** — content flagged as problematic; reply `stop` to abort or provide unblock instructions
- **Stage 2 theme approval** — new theme or framework detected; confirm or rename before the vault entry is created

---

## Project structure

```
painforwisdom/
├── .claude/
│   ├── agents/          # Specialized subagents (one per stage)
│   └── skills/          # Slash command skills (/retry-failed, /extract-transcription)
├── obsidian-vault/      # Git submodule → gonandrap/painforwisdom-kb (draft branch)
├── processed/           # Pipeline run outputs (gitignored)
├── to_be_retried/       # Failed/weak transcripts queued for retry (gitignored)
├── CLAUDE.md            # Full pipeline orchestration spec
├── run-pipeline.sh      # Entry point for automated/bulk runs
├── extract_transcription.sh  # Whisper-based transcription extraction
└── telegram_io.sh       # Telegram send/receive helpers
```
