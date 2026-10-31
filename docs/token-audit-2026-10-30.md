# Token-usage audit: Claude Code harness for painforwisdom (2026-10-30)

Scope: what every Claude Code session in this repo loads or does that costs tokens. Pick items; each becomes its own work order.

## Baseline (measured)

From the 4 most recent session transcripts under `~/.claude/projects/*painforwisdom*`:

| session | calls | context at 1st call | cache write | cache read | output |
|---|---|---|---|---|---|
| wo-7b4a7227 | 104 | 43,376 | 151,687 | 7,942,527 | 37,135 |
| wo-0b979fed | 47 | 45,411 | 122,511 | 3,394,628 | 33,694 |
| wo-b3967071 | 46 | 45,404 | 95,006 | 3,159,067 | 48,513 |
| wo-ffb343c0 (this) | 20 | 45,416 | 67,818 | 1,123,749 | 9,620 |

Every session starts at ~45k tokens before reading a file, and cache read dominates the bill. Each 1k tokens of fixed prefix costs roughly 50-100k cache-read tokens per work order (one re-read per call). The repo itself contributes little to that 45k. Most of it comes from global/account config and Jarvis.

## A. Repo-owned (this project)

A1. **No project `CLAUDE.md` exists.** No orientation, so every session explores to learn the layout (pipeline vs coach vs vault submodule, docker-only coach tests, `PYTHONPATH=services/coach`, conda env, where the specs live). Exploration is the biggest variable cost. Fix: a short CLAUDE.md (~40 lines, <1k tokens) with only pointers: component map, test commands, the "never commit inside obsidian-vault" rule, and the doc each area starts from. Duplicate nothing that is in README or OPERATIONS. Expected effect: fewer exploratory Read/Bash calls, and each saved call also saves one re-read of the ~45-80k context. Biggest lever in the repo.

A2. **9 pipeline agents in `.claude/agents/` show up in every session's Agent tool list** (~3.6 KB of descriptions, ~0.9k tokens per session, re-read every call). They are runtime system prompts that `pipeline/runtime.py:92` `load_agent_prompt` reads from `AGENTS_DIR`, not interactive subagents. Fix: move them to `pipeline/agents/` (or similar) and repoint `AGENTS_DIR`, `pipeline/themes_db.py` and `render_curator_taxonomy.py`. None of the 3 project skills reference them (checked).

A3. **`blog-post-catchy-title` agent is dead.** The stage was dropped and no `.py`/`.sh` references it, yet it is still listed in every session. Delete it (part of A2, or standalone).

A4. **Stale root docs bait reads.** `REPORT.md` (14.7 KB, an overnight build report from 2026-05-11 about an uncommitted tree) is tracked. The main checkout also has untracked `OPERATION.md` (12 KB, 10-29) next to tracked `OPERATIONS.md` (12.8 KB, 05-17), two near-duplicate names. An agent that sees both reads both. Fix: delete or move REPORT.md to `docs/archive/`, and merge OPERATION.md into OPERATIONS.md (or commit it under one name).

A5. **No Serena project (`.serena/project.yml` absent).** Jarvis tells every worker to `activate_project` and to use Serena first, but without project config Serena re-indexes on each activation, and `~/.claude/hooks/block-grep-code.sh` skips enforcement here. Workers fall back to grep plus whole-file reads. Fix: run Serena onboarding once and commit `.serena/project.yml` (ignore its cache dir). Trade-off: once the config exists, the Grep-block hook becomes active in this repo.

A6. **`services/coach/uv.lock` (820 KB) is tracked and not search-excluded.** Any broad `grep -r` or Grep over `services/coach` can hit it and flood the output. Fix: add a `.ignore` / `.rgignore` with `*.lock` and `docs/superpowers/plans/` (the plans are 40-85 KB each, which is fine on targeted reads and noisy on sweeps).

A7. **`.claude/settings.local.json` (main checkout) sets `defaultMode: bypassPermissions` and lists stale Notion permissions.** No token effect. Listed only because it is harness hygiene; skip unless wanted.

## B. Per-user auto-memory (`~/.claude/projects/.../memory/`)

B1. **`MEMORY.md` is 7.2 KB (~1.8k tokens) and loads in every session.** Its lines are ~200 chars and carry detail that belongs in the linked files. Fix: cut each line to a ≤80-char hook. Target ~2.5 KB, saving ~1.2k tokens per call.

B2. **Stale or contradictory entries in MEMORY.md** (they mislead and cost tokens):
- "Coach deploy state" says prod is v0.1.5. Later entries say v0.1.14 and v0.1.15 shipped.
- "Grounding-gate package landed (PR #52 resolved)" is history, not guidance.
- "Stream 3 ... PR #66 open (NOT deployed)": status unknown, needs a re-check.
- "Absolute paths in chat" duplicates `~/.claude/CLAUDE.md`.
- "blog-post-catchy-title dropped" becomes moot once A3 lands.

B3. **Two parallel memories.** Claude auto-memory (33 files) and the Jarvis KB (`jarvis learn`, 12 entries, e.g. kn-f7ffb135 project map, kn-e17e2272 status) overlap, and the Jarvis PostToolUse hook mirrors one into the other. Decide which is the source of truth for project facts and prune the other to user and feedback items only.

## C. Global user config (`~/.claude/settings.json`), affects every project

C1. **`"model": "opus[1m]"` + `"effortLevel": "high"` for every session**, including routine edits and lookups. This is the largest per-token cost multiplier. Options: keep Opus but default effort to `medium` and raise it per work order. Or let Jarvis pick model and effort per work-order type.
  - Resolution (wo-792f50f8): done, no change. Catalog already pins workers: `worker.model = "claude-opus-5-5"`, `worker.effort = "medium"`, `worker.investigation_model = "sonnet"` (`investigation_effort = "medium"`). Supervisor is set per project (`supervisor.model = "claude-opus-5"`). Neo (`os.neo.model`) and validation seats (`seat_models = {}`) are fleet-wide on purpose, review roles, so no project key proposed. User-level `opus[1m]`/`high` affects only interactive sessions, out of scope.

C2. **Enabled plugins whose fixed cost lands in every session**:
- `superpowers`: SessionStart injects the `using-superpowers` skill (3.2 KB, ~0.8k tokens) and adds ~15 skill descriptions. It also pushes extra skill invocations, each of which loads a full SKILL.md.
- `frontend-design`: this repo has almost no frontend (only the monitor UI). Disable it per project in `.claude/settings.json` `enabledPlugins`.
- `context7` + `~/.claude/rules/context7.md`: small, keep.
  - Resolution (wo-792f50f8): user-action. Keep serena, pyright-lsp, context7. Drop frontend-design for workers: `jarvis config set painforwisdom wiring.disabled_plugins '["cowork-plugin-management@synced","frontend-design@claude-plugins-official"]' --reason "audit C2: no frontend work in painforwisdom workers"` (saves ~0.1k tokens per call). Optional, user's call: superpowers. Spec 4.2's "unless shown unused" bar is met on data (0 superpowers skill invocations across all 10 painforwisdom worker transcripts, only `open-a-pull-request` x5 and `report-jarvis-bug` x3), but it is deliberately not auto-applied because it changes how workers work, not only cost (per Neo, Q2629). Saving ~1.6k tokens per start. Command, replacing the one above: `jarvis config set painforwisdom wiring.disabled_plugins '["cowork-plugin-management@synced","frontend-design@claude-plugins-official","superpowers@claude-plugins-official"]' --reason "audit C2: superpowers unused by painforwisdom workers (0 invocations in 10 orders)"`.

C3. **Account-level tools not configured in this repo**: the Artifact tool description (~4k tokens), claude-in-chrome (~25 deferred tools plus a system-prompt section), easel, `anthropic-skills:*` (~20 skill descriptions: docx/pptx/xlsx/pdf/canvas/morning...). None are used by this project's work orders. Check whether they can be disabled per project (`disabledMcpjsonServers`, plugin toggles, or Jarvis launch flags). This could be the largest fixed chunk of the 45k baseline.
  - Resolution (wo-792f50f8): user-action plus 2 Jarvis gaps. Baseline: first call of the last 5 workers is 43.4k tokens. Measured on Claude Code 2.1.314 with haiku `claude --bg` probes (workers run as `--bg`; plain `claude -p` gets none of these tools): no deny 46,287, deny list below 33,523 (-12.8k), plus `--no-chrome` 31,907 (-1.6k more). No painforwisdom worker transcript ever called any of these tools. Command (carries the current 5 defaults): `jarvis config set painforwisdom worker.denied_tools '["Workflow","ScheduleWakeup","ReportFindings","ListAgents","Agent(statusline-setup)","Artifact","ArtifactComments","ArtifactData","DesignSync","SendUserFile","PushNotification","RemoteTrigger","CronCreate","CronDelete","CronList","mcp__claude-in-chrome","mcp__easel","mcp__claude-artifacts"]' --reason "audit C3: account tools painforwisdom workers never use"`. Gaps: Chrome system-prompt section survives the deny, and only `--no-chrome` removes it (`claudeInChromeDefaultEnabled` lives in global config, so `settings_overrides` cannot carry it): https://github.com/gonandrap/agentic_os/issues/1370. `anthropic-skills:*` (~2.6k tokens) has no lever and is not on the wiring page: https://github.com/gonandrap/agentic_os/issues/1371. No `settings_overrides` change proposed, so section 3's command stands alone. Check after applying: `jarvis config wiring painforwisdom --refresh`, then the next order's first-call context (`jarvis inspect <wo-id>` or its transcript), expected ~30k vs 43.4k.

C4. **`autoMode.environment` block** (~3 KB) is stale ("no remotes configured", "zero tracked files"). Its token cost is the classifier's, not the main context's. Low priority, but it is also wrong.
  - Resolution (wo-792f50f8): skipped. `settings_overrides` is written to the shared project `.claude/settings.json` (Jarvis `bootstrap.inject_settings`) and to the worker's `--settings` file. Claude Code does not read `autoMode` from shared project settings, and no cheap probe shows the `--settings` copy reaches the classifier, so spec 4.4's condition is not met. The stale user-level block is the user's to fix by hand.

## D. Jarvis OS (separate project, report only)

D1. Per-WO prompt plus house style plus Serena/navigation sections are ~3-4k tokens per session. The prompt duplicates parts of the contract that `jarvis brief` already serves on demand.

D2. The "say in a few words what you're doing" nudge forces extra assistant turns. Each one re-reads the full context.

D3. The `.claude/settings.json` hook `$comment` fields are large, but they are not sent to the model. No cost.

## E. Runtime (not the Claude Code harness, out of scope, noted)

E1. `pipeline/runtime.py` `CACHE_PADDING_APPENDIX` pads agent prompts on purpose to clear the Sonnet cache floor. Revisit if the model changes.

E2. `pipeline/cost_forecast.py`, `normalize_themes.py` and `poc_brief*.py` pin `claude-sonnet-4-6`. Coach latency and model work is already tracked separately.

## Suggested order

1. A1 CLAUDE.md, B1+B2 memory trim: low effort, every session.
2. C3 account tools and C2 plugins: possibly the biggest fixed cut, needs investigation.
3. C1 effort default: one-line change, biggest multiplier, quality trade-off to decide.
4. A2+A3 move/delete agents, A4 stale docs, A6 search ignores.
5. A5 Serena project, B3 memory source of truth.
