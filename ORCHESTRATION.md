# PainForWisdom Operating Model (Hybrid: OpenClaw + Claude)

## Goal
Run the content pipeline with:
- **Claude Code** or **Gemini CLI** doing heavy generation/refactoring work
- **OpenClaw** acting as orchestration + human-in-the-loop control plane (mobile approvals via WhatsApp/Telegram)

This keeps current velocity while removing "I need to be at terminal" bottlenecks.

---

## Core Principles

1. **Single orchestrator**: OpenClaw is the source of truth for run state and user approvals.
2. **Claude as worker runtime**: Existing Claude agents and skills remain primary execution engine.
3. **Artifact-first pipeline**: Every stage writes files; next stage consumes files.
4. **No hidden state**: Decisions/approvals are logged to run artifacts and (optionally) Notion.
5. **Review before merge**: KB changes always go through branch + PR.

---

## Responsibilities

## OpenClaw (Orchestrator)
- Trigger pipeline runs from chat
- Create branch and run IDs
- Invoke Claude pipeline wrapper scripts
- Detect pauses / approval requests
- Ask user via WhatsApp/Telegram
- Feed answers back to Claude process
- Collect artifacts and summarize outcomes
- Open PR for KB/content diffs

## Claude/Gemini Pipeline (Worker)
- Run existing `.claude/agents/*` or `.gemini/agents/*` flow
- Produce stage outputs under `processed/<RUN_ID>/...`
- Emit structured prompts when human input is required
- Stop/continue based on orchestrator-provided input

---

## Run Lifecycle

1. **Trigger**
   - User asks OpenClaw to run pipeline for transcript/video/bulk set.

2. **Preparation**
   - OpenClaw creates branch: `pipeline/<date>/<slug>`
   - OpenClaw creates run dir and metadata file.

3. **Execution**
   - OpenClaw launches Claude pipeline script (non-interactive mode).
   - Claude stages execute sequentially as currently designed.

4. **Human-in-the-loop checkpoints**
   - If pipeline needs input, Claude writes a structured request (see contract below).
   - OpenClaw notifies user in chat, captures response, writes response file.
   - Pipeline resumes.

5. **Completion**
   - OpenClaw validates artifacts.
   - OpenClaw posts summary + key diffs.
   - OpenClaw opens PR for review.

6. **Merge**
   - User approves PR manually.
   - Optional post-merge sync/cleanup automation.

---

## Human Input Contract (Hook Interface)

To avoid brittle terminal prompts, use files in run dir:

- Request file: `processed/<RUN_ID>/hitl/request.json`
- Response file: `processed/<RUN_ID>/hitl/response.json`

### request.json (written by Claude-side wrapper)
```json
{
  "question_id": "uuid-or-stage-id",
  "stage": "kb-curator",
  "prompt": "Approve creating new theme 'pattern-x'?",
  "options": ["yes", "no", "rename"],
  "timeout_seconds": 1800,
  "default": "no",
  "context_files": [
    "processed/<RUN_ID>/.../extraction_report.md"
  ]
}
```

### response.json (written by OpenClaw)
```json
{
  "question_id": "same-id",
  "answer": "yes",
  "answered_by": "gonzalo",
  "answered_at": "2026-03-04T20:00:00Z"
}
```

Wrapper behavior:
- block until response exists or timeout
- on timeout: apply default and log decision

---

## Git Workflow (Required)

For each run:
1. `git checkout -b pipeline/<date>/<slug>`
2. Run pipeline
3. Commit only deterministic artifacts and vault/content changes
4. Push branch
5. Open PR to `main`
6. User reviews KB impact before merge

### Suggested commit grouping
- Commit 1: pipeline code/config changes (if any)
- Commit 2: generated content + vault updates

---

## Repo Conventions to Adopt Next

1. **Non-interactive mode** in `run-pipeline.sh`
   - Replace `read -p` confirmations with flags (`--yes`, `--no-input`, `--llm`).

2. **Environment-safe paths**
   - Remove hardcoded macOS path in `.claude/skills/extract-transcription/SKILL.md`.

3. **Run manifest**
   - Add `processed/<RUN_ID>/manifest.json` containing transcript, stages, status, approvals.

4. **PR template**
   - Include: entry files added, themes touched, frameworks touched, notion side effects.

5. **Approval log**
   - Append to `processed/<RUN_ID>/approvals.log` for auditability.

---

## Agent Strategy (Now)

Use **hybrid** approach for now:
- Keep current Claude agents in `.claude/agents` as execution specialists.
- Use OpenClaw as orchestrator + communication layer.
- Promote only stable cross-run roles to first-class later (e.g., pipeline-controller, notifier, PR-review summarizer).

---

## Model Usage Strategy

- **Claude runtime**: generation/refactoring-intensive stages
- **OpenAI/OpenClaw runtime**: orchestration, summaries, user interaction, planning, QA checks

This intentionally uses both subscriptions/credits where each is strongest.

---

## Definition of Done for a Pipeline Run

A run is complete when all are true:
- Required stage artifacts exist
- HITL decisions (if any) are logged
- Git branch pushed
- PR opened with summary of KB/content impact
- User receives final run report in chat

