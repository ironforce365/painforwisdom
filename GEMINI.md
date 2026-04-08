# Project: Gonzalo's Content Pipeline (Gemini Edition)

## Overview
This project automates the creation of blog posts for painforwisdom.wordpress.com
from raw video transcripts recorded during runs, and builds a structured Obsidian
knowledge base that will serve as the foundation for Gonzalo's book.

@MEMORY.md

## Architecture
The main Gemini CLI session acts as the orchestrator. Subagents never call each
other. Gemini CLI invokes each subagent sequentially, collects its output,
verifies it, and passes it as input to the next stage. All pipeline logic lives
here in GEMINI.md, not inside any subagent.

## Telegram I/O (async human input)

When the pipeline needs Gonzalo's input, **never block waiting for terminal input**.
Instead, use `telegram_io.sh` to send a message and wait for the reply asynchronously.
This allows Gonzalo to step away from the computer and respond from his phone.

```bash
# Send a question and wait indefinitely for a reply (no timeout)
REPLY=$(./telegram_io.sh ask "<your question here>")
echo "Gonzalo replied: $REPLY"
```

- `./telegram_io.sh send "<text>"` — fire-and-forget notification
- `./telegram_io.sh wait_reply` — poll indefinitely until reply arrives
- `./telegram_io.sh ask "<text>"` — send + wait indefinitely (most common)

**There is no timeout. Wait as long as needed for Gonzalo's reply.**

Credentials are loaded from `.env` (never commit that file).
If the script errors (missing credentials, network issue), fall back to reporting
the blocker in the terminal and stopping the pipeline — do not silently continue.

## Subagents
The following subagents are available in `.gemini/agents/`:
- `coaching-thought-extractor` — analyzes transcripts, extracts coaching insights
- `painforwisdom-writer` — writes blog posts mimicking the painforwisdom style
- `kb-curator` — maintains the Obsidian vault and evolves the book outline
- `research-curator` — finds and verifies specific references, saves to vault
- `notion-research-logger` — creates Notion tasks from research reports
- `notion-blog-post-logger` — logs the generated blog post to the Notion "Blog post pending publications" database
- `blog-post-catchy-title` — revisits the blog post title in Notion for marketing appeal while keeping the blog's voice
- `pipeline-summary` — final stage: reads pipeline.log, sends Telegram summary, writes summary to disk

## Notion
Research Tasks database: https://www.notion.so/64b70c23f694412895b72a383001c0f2
Data source ID: dfd97a4e-0114-4cb8-8f75-658bb2b83b17

---

## PIPELINE ORCHESTRATION

### Run directory

The `RUN_ID` and `LOG_FILE` are **always provided in the input** by `run-pipeline.sh`.
Do NOT create a new `RUN_ID`. Use exactly the values passed in.

At the start of processing each transcript, capture the vault path and create
the per-file run directory:
```bash
VAULT_PATH=$(pwd)/obsidian-vault
mkdir -p ./processed/$RUN_ID/$INPUT_TRANSCRIPT
```

Immediately after, **execute this Bash command** to notify Gonzalo:
```bash
./telegram_io.sh send "🚀 Pipeline started — $INPUT_TRANSCRIPT\nRun ID: $RUN_ID"
echo "$(date +%Y-%m-%dT%H:%M:%S) TELEGRAM_SENT msg=pipeline_start file=$INPUT_TRANSCRIPT" >> $LOG_FILE
```

All subagents write their output under `./processed/$RUN_ID/$INPUT_TRANSCRIPT/`.
The `pipeline.log` lives one level up at `./processed/$RUN_ID/pipeline.log` and
covers the entire run (all files). Pass the full paths to each subagent.

---

### How to trigger

**Full pipeline (kb + blog post):**
"run the content pipeline on this transcript [paste transcript]"

**KB only, no blog post:**
"run the knowledge base pipeline on this transcript, no blog post needed
Video date: YYYY-MM-DD [paste transcript]"

**Bulk ingestion:**
"process all transcripts in [directory path]"
Files must follow the naming convention: transcript_YYYY-MM-DD.txt
---

### Execution rules (always follow these)

1. Never simulate or describe agent invocations — invoke them as real `@subagent` calls
2. Create the run directory before invoking any subagent
3. Pass the full run directory path to every subagent
4. Invoke one subagent at a time — never in parallel
5. After each subagent completes, verify its output file exists on disk
6. Fully read each subagent's output file before invoking the next stage
7. If a required stage fails verification, stop and report — do not continue
8. Pass explicit input to each subagent — never assume they share context
9. **Never fall back to a general-purpose agent when a specialized agent is unavailable.** If a named agent (@coaching-thought-extractor, @kb-curator, @research-curator, @notion-research-logger, @notion-blog-post-logger, @blog-post-catchy-title, @painforwisdom-writer) cannot be invoked, stop the pipeline immediately and report which agent failed to load. Do not substitute, approximate, or continue with any other agent type.
10. **Telegram notifications are mandatory.** Every stage completion and every input request MUST trigger a real Bash tool call to `./telegram_io.sh`. Never skip, simulate, or defer these calls. They are not optional logging — they are the only way Gonzalo knows the pipeline is progressing while away from the computer.
11. **Log every significant event to `$LOG_FILE`.** After every stage completion, skip, failure, Telegram send, Telegram ask, and Telegram reply, append a structured line to `$LOG_FILE` using `echo "$(date +%Y-%m-%dT%H:%M:%S) EVENT ..." >> $LOG_FILE`. This log is the only way to reconstruct what happened after the fact.
12. **Always include `$INPUT_TRANSCRIPT` in every Telegram message**, so Gonzalo can identify which file a message refers to when processing a bulk run.

---

### Stage 1 — @coaching-thought-extractor

**Invoke with:**
- Full transcript text
- Video date
- Run directory path: `./processed/$RUN_ID/$INPUT_TRANSCRIPT`
- Transcript file name
- Transcript file content

**Agent writes to:** `./processed/$RUN_ID/$INPUT_TRANSCRIPT/coaching-thought-extractor/extraction_report.md`

**Verify:**
```bash
FILE=./processed/$RUN_ID/$INPUT_TRANSCRIPT/coaching-thought-extractor/extraction_report.md
if [ -f $FILE ]; then
    echo "Summary file exists"
else
    echo "Summary file not found"
fi
```
- File exists and contains Content Quality, Core Insight, Blog Post Seed → continue
- File missing or incomplete → re-invoke extractor once, then if still failing **execute these Bash commands** and stop:
  ```bash
  mkdir -p ./to_be_retried
  cp $TRANSCRIPT_FILE ./to_be_retried/
  ./telegram_io.sh send "❌ Stage 1 failed — $INPUT_TRANSCRIPT\nExtraction failed after retry.\nTranscript copied to to_be_retried/ for manual retry."
  echo "$(date +%Y-%m-%dT%H:%M:%S) STAGE_1_FAILED reason=extraction_failed" >> $LOG_FILE
  echo "$(date +%Y-%m-%dT%H:%M:%S) FAILED_FILE_QUEUED file=$INPUT_TRANSCRIPT" >> $LOG_FILE
  echo "$(date +%Y-%m-%dT%H:%M:%S) TELEGRAM_SENT msg=stage1_failed" >> $LOG_FILE
  ```

**CRITICAL: Never write or create the extraction_report.md yourself.** If the file is missing, only re-invoke the @coaching-thought-extractor agent. Writing the file directly bypasses the extraction logic and corrupts the pipeline.

**Read** the Content Quality field from the file.

**On success:** after verifying the file exists and reading Content Quality, **execute these Bash commands** (substitute actual quality):
```bash
./telegram_io.sh send "✅ Stage 1 complete — $INPUT_TRANSCRIPT\nCoaching thought extracted\nQuality: Strong"
echo "$(date +%Y-%m-%dT%H:%M:%S) STAGE_1_COMPLETE quality=Strong" >> $LOG_FILE
echo "$(date +%Y-%m-%dT%H:%M:%S) TELEGRAM_SENT msg=stage1_complete" >> $LOG_FILE
```

**Gate:**
- Flagged → **execute this Bash command**, substituting the full flag reasons and unblock questions read from `extraction_report.md`:
  ```bash
  REPLY=$(./telegram_io.sh ask "🚩 FLAGGED — $INPUT_TRANSCRIPT\n\nReasons:\n- <reason 1>\n- <reason 2>\n\nTo unblock:\n1. <question 1>\n2. <question 2>\n\nReply 'continue' to proceed anyway, or 'stop' to abort.")
  echo "$(date +%Y-%m-%dT%H:%M:%S) TELEGRAM_ASK topic=flag_review file=$INPUT_TRANSCRIPT" >> $LOG_FILE
  echo "$(date +%Y-%m-%dT%H:%M:%S) TELEGRAM_REPLY reply=$REPLY" >> $LOG_FILE
  ```
  If reply is `stop` → log `STAGE_1_ABORTED reason=flagged_by_gonzalo` and abort pipeline.
  If reply is `continue` → log `STAGE_1_FLAG_OVERRIDDEN` and proceed to Stage 2.
- Weak or Strong → continue to Stage 2

---

### Stage 2 — @kb-curator

**Invoke with:**
- Full content of `./processed/$RUN_ID/$INPUT_TRANSCRIPT/coaching-thought-extractor/extraction_report.md`
- Video date
- Run directory path: `./processed/$RUN_ID/$INPUT_TRANSCRIPT`
- Vault path: `$VAULT_PATH`

**Agent writes to:** `./processed/$RUN_ID/$INPUT_TRANSCRIPT/kb-curator/curator_summary.md`

**Verify output file:**
```bash
FILE=./processed/$RUN_ID/$INPUT_TRANSCRIPT/kb-curator/curator_summary.md
if [ -f $FILE ]; then echo "exists"; else echo "not found"; fi
```

**Verify vault side effect:**
```bash
ls ./obsidian-vault/gonzalo-book/entries/YYYY-MM-DD-*.md 2>/dev/null
```
- Entry file exists → **detect new themes/frameworks, then execute these Bash commands** to complete Stage 2.
- Entry file missing → **execute these Bash commands** and stop (see CLAUDE.md for error logic).

**MANDATORY approval check:** After every @kb-curator invocation, before proceeding,
scan the agent's output for the string `⚠️ NEW THEME DETECTED` or
`⚠️ NEW FRAMEWORK DETECTED`. If either is present:

1. The agent has written nothing to disk.
2. **Execute this Bash command** to ask Gonzalo for approval via Telegram.
3. Re-invoke @kb-curator with the original input PLUS Gonzalo's reply appended.
4. Repeat until no more approvals are pending.

---

### Stage 3 — @painforwisdom-writer

**Only runs if:**
- Pipeline mode is Full
- Content Quality in `extraction_report.md` is Strong

**Invoke with:**
- Blog Post Seed field from `extraction_report.md`
- Video date (YYYY-MM-DD)
- Run directory path: `./processed/$RUN_ID/$INPUT_TRANSCRIPT`

**Agent writes to:** `./processed/$RUN_ID/$INPUT_TRANSCRIPT/painforwisdom-writer/blog_post.md`

---

### Stage 4 — @notion-blog-post-logger

**Only runs if:** Stage 3 produced a `blog_post.md`

**Invoke with:**
- Full content of `blog_post.md`
- Video date
- Run directory path: `./processed/$RUN_ID/$INPUT_TRANSCRIPT`

---

### Stage 5 — @blog-post-catchy-title

**Only runs if:** Stage 4 produced a `notion_blog_summary.md` with a Notion URL

**Invoke with:**
- Notion page URL from `notion_blog_summary.md`
- Vault path: `$VAULT_PATH`
- Run directory path: `./processed/$RUN_ID/$INPUT_TRANSCRIPT`

---

### Stage 6 — @research-curator

**Invoke with:**
- Filename of the entry created in Stage 2.
- Content of the entry file
- Run directory path: `./processed/$RUN_ID/$INPUT_TRANSCRIPT`

---

### Stage 7 — @notion-research-logger

**Invoke with:**
- Full contents of `research_report.csv`
- Full contents of the vault entry file
- Run directory path: `./processed/$RUN_ID/$INPUT_TRANSCRIPT`

---

### Stage 8 — @pipeline-summary (MANDATORY)

**Invoke with:**
- Run directory path: `./processed/$RUN_ID/$INPUT_TRANSCRIPT`
- `INPUT_TRANSCRIPT`
- `RUN_ID`
- `LOG_FILE`
- `PROJECT_ROOT`

---

## painforwisdom-writer context
(Same as CLAUDE.md)
