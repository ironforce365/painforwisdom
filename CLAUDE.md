# Project: Gonzalo's Content Pipeline

## Overview
This project automates the creation of blog posts for painforwisdom.wordpress.com
from raw video transcripts recorded during runs, and builds a structured Obsidian
knowledge base that will serve as the foundation for Gonzalo's book.

@MEMORY.md

## Architecture
The main Claude Code session acts as the orchestrator. Subagents never call each
other. Claude Code invokes each subagent sequentially, collects its output,
verifies it, and passes it as input to the next stage. All pipeline logic lives
here in CLAUDE.md, not inside any subagent.

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
The following subagents are available in `.claude/agents/`:
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

Directory structure for a bulk run processing two files:
```
./processed/
└── 2026-02-26_143022/              ← one per run-pipeline.sh invocation
    ├── pipeline.log                ← master event log for all files in this run
    ├── transcript_2026-02-17/      ← per-file output
    │   ├── coaching-thought-extractor/
    │   │   └── extraction_report.md
    │   ├── kb-curator/
    │   │   └── curator_summary.md
    │   ├── painforwisdom-writer/
    │   │   └── blog_post.md
    │   ├── notion-blog-post-logger/
    │   │   └── notion_blog_summary.md
    │   ├── blog-post-catchy-title/
    │   │   └── title_update_summary.md
    │   ├── research-curator/
    │   │   └── research_report.csv
    │   ├── notion-research-logger/
    │   │   └── notion_summary.md
    │   └── pipeline-summary/
    │       └── pipeline_summary.md
    └── transcript_2026-02-18/      ← second file, same structure
        └── ...
```

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

1. Never simulate or describe agent invocations — invoke them as real tool calls
2. Create the run directory before invoking any subagent
3. Pass the full run directory path to every subagent
4. Invoke one subagent at a time — never in parallel
5. After each subagent completes, verify its output file exists on disk
6. Fully read each subagent's output file before invoking the next stage
7. If a required stage fails verification, stop and report — do not continue
8. Pass explicit input to each subagent — never assume they share context
9. **Never fall back to a general-purpose agent when a specialized agent is unavailable.** If a named agent (coaching-thought-extractor, kb-curator, research-curator, notion-research-logger, notion-blog-post-logger, blog-post-catchy-title, painforwisdom-writer) cannot be invoked, stop the pipeline immediately and report which agent failed to load. Do not substitute, approximate, or continue with any other agent type.
10. **Telegram notifications are mandatory.** Every stage completion and every input request MUST trigger a real Bash tool call to `./telegram_io.sh`. Never skip, simulate, or defer these calls. They are not optional logging — they are the only way Gonzalo knows the pipeline is progressing while away from the computer.
11. **Log every significant event to `$LOG_FILE`.** After every stage completion, skip, failure, Telegram send, Telegram ask, and Telegram reply, append a structured line to `$LOG_FILE` using `echo "$(date +%Y-%m-%dT%H:%M:%S) EVENT ..." >> $LOG_FILE`. This log is the only way to reconstruct what happened after the fact.
12. **Always include `$INPUT_TRANSCRIPT` in every Telegram message**, so Gonzalo can identify which file a message refers to when processing a bulk run.

---

### Stage 1 — coaching-thought-extractor

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

**CRITICAL: Never write or create the extraction_report.md yourself.** If the file is missing, only re-invoke the coaching-thought-extractor agent. Writing the file directly bypasses the extraction logic and corrupts the pipeline.

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

### Stage 2 — kb-curator

**Invoke with:**
- Full content of `./processed/$RUN_ID/$INPUT_TRANSCRIPT/coaching-thought-extractor/extraction_report.md`
- Video date
- Run directory path: `./processed/$RUN_ID/$INPUT_TRANSCRIPT`
- Vault path: `$VAULT_PATH` (the absolute path computed at pipeline start, e.g. `/Users/gonzalo.raposo/workspace/painforwisdom/obsidian-vault`)

**Agent writes to:** `./processed/$RUN_ID/$INPUT_TRANSCRIPT/kb-curator/curator_summary.md`

**Verify output file:**
```bash
FILE=./processed/$RUN_ID/$INPUT_TRANSCRIPT/kb-curator/curator_summary.md
if [ -f $FILE ]; then
    echo "exists"
else
    echo "not found"
fi
```
- File exists → continue to vault verification
- File missing → **execute these Bash commands** and stop:
  ```bash
  mkdir -p ./to_be_retried
  cp $TRANSCRIPT_FILE ./to_be_retried/
  ./telegram_io.sh send "❌ Stage 2 failed — $INPUT_TRANSCRIPT\nKB curator did not produce output.\nTranscript copied to to_be_retried/ for manual retry."
  echo "$(date +%Y-%m-%dT%H:%M:%S) STAGE_2_FAILED reason=curator_summary_missing" >> $LOG_FILE
  echo "$(date +%Y-%m-%dT%H:%M:%S) FAILED_FILE_QUEUED file=$INPUT_TRANSCRIPT" >> $LOG_FILE
  echo "$(date +%Y-%m-%dT%H:%M:%S) TELEGRAM_SENT msg=stage2_failed" >> $LOG_FILE
  ```

**Verify vault side effect:**
```bash
ls ./obsidian-vault/gonzalo-book/entries/YYYY-MM-DD-*.md 2>/dev/null
```
- Entry file exists → **execute these Bash commands**, then continue to Stage 3:
  ```bash
  ./telegram_io.sh send "✅ Stage 2 complete — $INPUT_TRANSCRIPT\nKnowledge base updated\nVault entry: $FILE_ENTRY"
  echo "$(date +%Y-%m-%dT%H:%M:%S) STAGE_2_COMPLETE vault_entry=$FILE_ENTRY" >> $LOG_FILE
  echo "$(date +%Y-%m-%dT%H:%M:%S) TELEGRAM_SENT msg=stage2_complete" >> $LOG_FILE
  ```
- Entry file missing → **execute these Bash commands** and stop:
  ```bash
  mkdir -p ./to_be_retried
  cp $TRANSCRIPT_FILE ./to_be_retried/
  ./telegram_io.sh send "❌ Stage 2 failed — $INPUT_TRANSCRIPT\nVault entry not created.\nTranscript copied to to_be_retried/ for manual retry."
  echo "$(date +%Y-%m-%dT%H:%M:%S) STAGE_2_FAILED reason=vault_entry_missing" >> $LOG_FILE
  echo "$(date +%Y-%m-%dT%H:%M:%S) FAILED_FILE_QUEUED file=$INPUT_TRANSCRIPT" >> $LOG_FILE
  echo "$(date +%Y-%m-%dT%H:%M:%S) TELEGRAM_SENT msg=stage2_failed" >> $LOG_FILE
  ```

**Note:** kb-curator may pause for theme/framework approval. When it does,
**execute this Bash command**, substituting the actual question and theme name:
```bash
REPLY=$(./telegram_io.sh ask "📚 KB Curator — $INPUT_TRANSCRIPT\n\n<paste curator's question>\n\nReply with your answer.")
echo "$(date +%Y-%m-%dT%H:%M:%S) TELEGRAM_ASK topic=theme_approval file=$INPUT_TRANSCRIPT" >> $LOG_FILE
echo "$(date +%Y-%m-%dT%H:%M:%S) TELEGRAM_REPLY reply=$REPLY" >> $LOG_FILE
```
Pass Gonzalo's reply back to kb-curator as additional input, then continue.

---

### Stage 3 — painforwisdom-writer

**Only runs if:**
- Pipeline mode is Full (not KB only)
- Content Quality in `./processed/$RUN_ID/$INPUT_TRANSCRIPT/coaching-thought-extractor/extraction_report.md` is Strong

**Invoke with:**
- Blog Post Seed field read from `./processed/$RUN_ID/$INPUT_TRANSCRIPT/coaching-thought-extractor/extraction_report.md`
- Video date (YYYY-MM-DD) — the date of the original recording, not today's date
- Run directory path: `./processed/$RUN_ID/$INPUT_TRANSCRIPT`

**Agent writes to:** `./processed/$RUN_ID/$INPUT_TRANSCRIPT/painforwisdom-writer/blog_post.md`

**Verify output file:**
```bash
FILE=./processed/$RUN_ID/$INPUT_TRANSCRIPT/painforwisdom-writer/blog_post.md
if [ -f $FILE ]; then
    echo "exists"
else
    echo "not found"
fi
```
- File exists and contains a title and body → **execute these Bash commands**, then continue:
  ```bash
  ./telegram_io.sh send "✅ Stage 3 complete — $INPUT_TRANSCRIPT\nBlog post written"
  echo "$(date +%Y-%m-%dT%H:%M:%S) STAGE_3_COMPLETE" >> $LOG_FILE
  echo "$(date +%Y-%m-%dT%H:%M:%S) TELEGRAM_SENT msg=stage3_complete" >> $LOG_FILE
  ```
- File missing or empty → log `STAGE_3_FAILED`, re-invoke writer once, then report

If stage 3 is skipped due to **Weak quality**, **execute these Bash commands** (substitute the actual transcript file path passed as input):
```bash
mkdir -p ./to_be_retried
cp $TRANSCRIPT_FILE ./to_be_retried/
./telegram_io.sh send "⚠️ Weak content — $INPUT_TRANSCRIPT\nNo blog post generated.\nTranscript copied to to_be_retried/ for your review."
echo "$(date +%Y-%m-%dT%H:%M:%S) STAGE_3_SKIPPED reason=Weak" >> $LOG_FILE
echo "$(date +%Y-%m-%dT%H:%M:%S) WEAK_FILE_QUEUED file=$INPUT_TRANSCRIPT" >> $LOG_FILE
echo "$(date +%Y-%m-%dT%H:%M:%S) TELEGRAM_SENT msg=weak_content" >> $LOG_FILE
```

If stage 3 is skipped due to **KB-only mode**, just log it:
```bash
echo "$(date +%Y-%m-%dT%H:%M:%S) STAGE_3_SKIPPED reason=KB_only" >> $LOG_FILE
```

---

### Stage 4 — notion-blog-post-logger

**Only runs if:** Stage 3 (painforwisdom-writer) produced a blog_post.md

**Invoke with:**
- Full content of `./processed/$RUN_ID/$INPUT_TRANSCRIPT/painforwisdom-writer/blog_post.md`
- Video date
- Run directory path: `./processed/$RUN_ID/$INPUT_TRANSCRIPT`

**Agent writes to:** `./processed/$RUN_ID/$INPUT_TRANSCRIPT/notion-blog-post-logger/notion_blog_summary.md`

**Verify output file:**
```bash
FILE=./processed/$RUN_ID/$INPUT_TRANSCRIPT/notion-blog-post-logger/notion_blog_summary.md
if [ -f $FILE ]; then
    echo "exists"
else
    echo "not found"
fi
```
- File exists and contains a Notion URL → **execute these Bash commands**, then continue to Stage 5:
  ```bash
  ./telegram_io.sh send "✅ Stage 4 complete — $INPUT_TRANSCRIPT\nBlog post logged to Notion"
  echo "$(date +%Y-%m-%dT%H:%M:%S) STAGE_4_COMPLETE" >> $LOG_FILE
  echo "$(date +%Y-%m-%dT%H:%M:%S) TELEGRAM_SENT msg=stage4_complete" >> $LOG_FILE
  ```
- File missing → log `STAGE_4_FAILED reason=file_missing`, continue (non-blocking)

If stage 4 is skipped (no blog post), log it:
```bash
echo "$(date +%Y-%m-%dT%H:%M:%S) STAGE_4_SKIPPED reason=no_post" >> $LOG_FILE
```

---

### Stage 5 — blog-post-catchy-title

**Only runs if:** Stage 4 (notion-blog-post-logger) produced a notion_blog_summary.md with a Notion URL

**Invoke with:**
- Notion page URL read from `./processed/$RUN_ID/$INPUT_TRANSCRIPT/notion-blog-post-logger/notion_blog_summary.md`
- Vault path: `$VAULT_PATH`
- Run directory path: `./processed/$RUN_ID/$INPUT_TRANSCRIPT`

**Agent writes to:** `./processed/$RUN_ID/$INPUT_TRANSCRIPT/blog-post-catchy-title/title_update_summary.md`

**Verify output file:**
```bash
FILE=./processed/$RUN_ID/$INPUT_TRANSCRIPT/blog-post-catchy-title/title_update_summary.md
if [ -f $FILE ]; then
    echo "exists"
else
    echo "not found"
fi
```
- File exists → **execute these Bash commands**, then continue to Stage 6:
  ```bash
  ./telegram_io.sh send "✅ Stage 5 complete — $INPUT_TRANSCRIPT\nTitle candidates generated"
  echo "$(date +%Y-%m-%dT%H:%M:%S) STAGE_5_COMPLETE" >> $LOG_FILE
  echo "$(date +%Y-%m-%dT%H:%M:%S) TELEGRAM_SENT msg=stage5_complete" >> $LOG_FILE
  ```
- File missing → log `STAGE_5_FAILED reason=file_missing`, continue (non-blocking)

If stage 5 is skipped (no blog post), log it:
```bash
echo "$(date +%Y-%m-%dT%H:%M:%S) STAGE_5_SKIPPED reason=no_post" >> $LOG_FILE
```

---

### Stage 6 — research-curator

**Invoke with:**
- Filename of the entry created in Stage 2. Entry file name is $FILE_ENTRY
- Content of $ENTRY_FILE
- Run directory path: `./processed/$RUN_ID/$INPUT_TRANSCRIPT`

**Agent writes to:** `./processed/$RUN_ID/$INPUT_TRANSCRIPT/research-curator/research_report.csv`

**Verify output file:**
```bash
FILE=./processed/$RUN_ID/$INPUT_TRANSCRIPT/research-curator/research_report.csv
if [ -f $FILE ]; then
    echo "exists"
else
    echo "not found"
fi
```
- File exists and has at least one data row → **execute these Bash commands** (substitute actual ref count), then continue to Stage 7:
  ```bash
  ./telegram_io.sh send "✅ Stage 6 complete — $INPUT_TRANSCRIPT\nResearch curated: 5 references found"
  echo "$(date +%Y-%m-%dT%H:%M:%S) STAGE_6_COMPLETE refs=5" >> $LOG_FILE
  echo "$(date +%Y-%m-%dT%H:%M:%S) TELEGRAM_SENT msg=stage6_complete" >> $LOG_FILE
  ```
- File missing or empty → log `STAGE_6_FAILED reason=file_missing`, continue to Stage 7

**Verify vault side effect:**
```bash
grep -l "## Research" ./obsidian-vault/gonzalo-book/entries/YYYY-MM-DD-*.md
```
- Section exists → continue
- Missing → log as non-blocking failure, continue

---

### Stage 7 — notion-research-logger

**Invoke with:**
- Full contents of `./processed/$RUN_ID/$INPUT_TRANSCRIPT/research-curator/research_report.csv`
- Run directory path: `./processed/$RUN_ID/$INPUT_TRANSCRIPT`

**Agent writes to:** `./processed/$RUN_ID/$INPUT_TRANSCRIPT/notion-research-logger/notion_summary.md`

**Verify output file:**
```bash
FILE=./processed/$RUN_ID/$INPUT_TRANSCRIPT/notion-research-logger/notion_summary.md
if [ -f $FILE ]; then
    echo "exists"
else
    echo "not found"
fi
```
- File exists → read task count from file, then **execute these Bash commands** (substitute actual task count):
  ```bash
  ./telegram_io.sh send "✅ Stage 7 complete — $INPUT_TRANSCRIPT\n5 research tasks created in Notion"
  echo "$(date +%Y-%m-%dT%H:%M:%S) STAGE_7_COMPLETE tasks=5" >> $LOG_FILE
  echo "$(date +%Y-%m-%dT%H:%M:%S) TELEGRAM_SENT msg=stage7_complete" >> $LOG_FILE
  ```
- File missing → log `STAGE_7_FAILED reason=file_missing`, continue (non-blocking)

**Verify:** task count in file matches reference count in research_report.csv
- Matches → continue
- Mismatches → log discrepancy, continue (non-blocking)

---

### Stage 8 — pipeline-summary (MANDATORY — always the last step, never skip)

**Invoke with:**
- Run directory path: `./processed/$RUN_ID/$INPUT_TRANSCRIPT`
- `INPUT_TRANSCRIPT`: the transcript name
- `RUN_ID`: the run identifier
- `LOG_FILE`: the master log path (e.g. `./processed/$RUN_ID/pipeline.log`)
- `PROJECT_ROOT`: absolute path to the project root (use `$(pwd)`)

**Agent writes to:** `./processed/$RUN_ID/$INPUT_TRANSCRIPT/pipeline-summary/pipeline_summary.md`

**Verify output file:**
```bash
FILE=./processed/$RUN_ID/$INPUT_TRANSCRIPT/pipeline-summary/pipeline_summary.md
if [ -f $FILE ]; then echo "exists"; else echo "not found"; fi
```
- File exists → print its contents to terminal, pipeline done
- File missing → log `STAGE_8_FAILED`, print terminal warning, pipeline done (non-blocking)

---

### Error escalation

| Stage | Failure | Action |
|-------|---------|--------|
| Any | Specialized agent not found or not registered | Stop pipeline immediately, report which agent failed to load — do NOT substitute with general-purpose |
| 1 | Output file missing or incomplete | Re-invoke once, then stop — **never write the file yourself** |
| 1 | Content Flagged | Send full flag reasons + unblock questions via Telegram; wait indefinitely; abort only if Gonzalo replies 'stop' |
| 2 | Output file missing | Stop pipeline, report |
| 2 | Vault entry file missing | Stop pipeline, report |
| 3 | Blog post file missing or empty | Re-invoke once, then report |
| 4 | Output file missing | Log, continue (non-blocking) |
| 5 | Output file missing | Log, continue (non-blocking) |
| 6 | Output file missing | Log, continue |
| 6 | Vault research section missing | Log, continue |
| 7 | Output file missing | Log, continue |
| 7 | Task count mismatch | Log, continue |
| 8 | pipeline-summary agent fails | Log STAGE_8_FAILED, print warning to terminal, pipeline still considered done |

---


## painforwisdom-writer context
Blog: https://painforwisdom.wordpress.com
Owner: Gonzalo — ultra runner, engineer, father
Style: raw first-person, date-stamped openings, bold key insights, bridges running
with life lessons, ends with earned 1-3 sentence conclusions, 400-600 words,
no headers or bullets in body. Always output a title before the post body.
Tone: grounded and direct, not heroic or dramatic. Reinforce facts and data from
what actually happened — don't exaggerate sensory details or make the experience
sound bigger than it was.
Citations: knows the world of David Goggins, Jocko Willink, Ed Mylett, Les Brown,
Eric Thomas, Tony Robbins, etc. Reference their concepts naturally when relevant,
but cite by name only occasionally and when it adds real power — over-citing feels forced.
