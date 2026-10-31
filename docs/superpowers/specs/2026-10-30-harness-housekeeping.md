# Harness housekeeping and Claude 5 modernization (2026-10-30)

Feature order fo-81a79f8d. Executes `docs/token-audit-2026-10-30.md` (the "audit"), one work order per audit section. The project is being revived after months idle (pipeline last ran 2026-05-18), so the goal is twofold: cut the fixed token cost every Claude Code session pays here, and bring the repo's Claude usage up to the 5th-generation models.

Read the audit item by item for your section; this spec records the decisions taken on top of it and overrides the audit where they disagree.

## 1. Shared rules for every child

1.1 **Resolution notes.** Each child records the outcome of every item in its audit section inside `docs/token-audit-2026-10-30.md`, as one indented sub-bullet directly under that item, starting `Resolution (wo-<id>):`. Values: done (what changed, file paths), skipped (why), or user-action (the exact command the user must run). Touch only your own section's items, append only (never reword item text), and never touch the audit's "Suggested order" list, so sibling PRs merge cleanly. This is the durable record; chat and work-order messages are not.

1.2 **The Jarvis catalog is read-only to workers.** Catalog: `/home/gonzalo/workspace/production/config/catalog.json`. A worker may run `jarvis config show|get|wiring` but never `jarvis config set|unset|restore` and never edits the file. Every catalog change is delivered as an exact command for the user, in the resolution note and in the final message, e.g. `jarvis config set painforwisdom wiring.disabled_plugins '["cowork-plugin-management@synced","frontend-design@claude-plugins-official"]' --reason "..."`. List-valued and object-valued keys are replaced whole by `set`, so the command must carry the current members plus the change (read them with `jarvis config get`).

1.3 **Project-scoped keys.** Project-only settings live under `projects.painforwisdom.*` in the catalog and are set with `jarvis config set painforwisdom <path> <value>`. If a setting you need exists only fleet-wide (`os.*`) or does not exist at all, that is a Jarvis gap: file it with the `report-jarvis-bug` skill and put the issue link in the resolution note. Do not work around it by editing user-level config.

1.4 **User-level config is off limits.** Never edit `~/.claude/settings.json`, `~/.claude/CLAUDE.md`, `~/.claude/rules/`, or any plugin/account setting. Filtering what reaches workers is done through Jarvis wiring and worker keys (section 4).

1.5 **Already true today** (verified 2026-10-30 with `jarvis config show`): `projects.painforwisdom.worker.model = "claude-opus-5-5"`, `worker.effort = "medium"`, `worker.investigation_model = "sonnet"`, `wiring.claude_ai_connectors = false`, `wiring.bundled_skills = false`, `wiring.disabled_plugins = ["cowork-plugin-management@synced"]`, `wiring.disabled_skills = ["heycrypto-pr","heycrypto_run_e2e"]`, `worker.denied_tools = ["Workflow","ScheduleWakeup","ReportFindings","ListAgents","Agent(statusline-setup)"]`, `settings_overrides = {"permissions":{"deny":[...]}}`. Re-read before writing a command; do not trust this list blindly.

1.6 **Git.** Worktree, PR, merge to main. Never commit inside `obsidian-vault/` (submodule). Never commit `OPERATION.md` (see 2.4).

## 2. Repo-owned cleanup (audit section A)

Covers A1-A7. Order inside the work order: structural moves first (2.2-2.5), then CLAUDE.md last (2.1) so it describes the final layout.

2.1 **CLAUDE.md (A1).** New `CLAUDE.md` at repo root, at most ~40 lines and under 1k tokens. Pointers only, nothing duplicated from README.md or OPERATIONS.md:
- component map: `pipeline/` (LangGraph DAG, conda env `painforwisdom-poc`), `services/coach/` (docker compose, Claude Agent SDK), `obsidian-vault/` (submodule, branch `draft`, never commit inside it), `pipeline/agents/` (stage prompts after 2.2).
- test commands: root `pytest tests/` for pipeline; coach tests run on host with `PYTHONPATH=services/coach` (CI matrix in `.github/workflows/ci.yml` is the reference); voice test needs `CUDA_VISIBLE_DEVICES=`.
- where each area starts reading: README.md, OPERATIONS.md, `services/coach/README.md`, `docs/superpowers/specs/`.
- dev flow in one line: worktree, PR, main, release; coach ships via `/coach-ship-it`.
No status, no versions, no dates: those rot (that is what B2 found in MEMORY.md).

2.2 **Move pipeline stage prompts out of `.claude/agents/` (A2).** `git mv .claude/agents/*.md pipeline/agents/`. They are runtime system prompts, not interactive subagents, and today they inflate every session's Agent tool list. Repoint every consumer:
- `pipeline/runtime.py` `AGENTS_DIR`
- `pipeline/cost_forecast.py` `AGENTS_DIR` (import it from `pipeline.runtime` instead of redefining)
- `pipeline/scripts/render_curator_taxonomy.py` `PROMPT_PATH`
- docstrings in `pipeline/themes_db.py`, `pipeline/scripts/render_curator_taxonomy.py`
- `OPERATIONS.md` file table (line ~282) and README.md if it names the path
Before moving, `grep -rn "\.claude/agents"` across the repo excluding `obsidian-vault/` and `docs/superpowers/plans/` to catch other consumers (skills, shell scripts, systemd units under `scripts/` or `deploy/`). `tests/test_stale_ref_resilience.py:53` uses `.claude/agents` only as a sample path string; leave it. Remove the now-empty `.claude/agents/`.

2.3 **Delete dead prompts (A3).** Delete, do not move: `blog-post-catchy-title.md` (stage dropped on purpose), and `notion-blog-post-logger.md`, `notion-research-logger.md`, `pipeline-summary.md` (Paperclip-era prompts: their nodes `pipeline/nodes/notion_blog.py`, `notion_research.py`, `validators/summarizer.py` are deterministic Python and only reuse the names as run-dir folder names; `load_agent_prompt` never reads them). Confirm with grep before deleting; if any is loaded, move it instead. Five prompts move: coaching-thought-extractor, kb-curator, painforwisdom-writer, research-curator, youtube-upload-agent.

2.3a **Guard test.** Add `tests/test_agent_prompts.py`: parametrized over the five moved filenames, each loads via `load_agent_prompt` non-empty with no frontmatter; `.claude/agents/` does not exist. Section 6 extends this file.

2.4 **Stale root docs (A4).** `git mv REPORT.md docs/archive/2026-05-11-wordpress-youtube-build-report.md`. The audit's advice to merge `OPERATION.md` into `OPERATIONS.md` is WRONG: `OPERATION.md` in the main checkout is generated by Jarvis OS (header "Generated by Jarvis OS (template v16)") and Jarvis never overwrites a committed copy, so committing it would freeze it. Add `OPERATION.md` to `.gitignore` instead, and leave `OPERATIONS.md` alone.

2.5 **Search excludes (A6).** Add a root `.ignore` (read by ripgrep and Claude Code's Grep) with `*.lock` and `docs/superpowers/plans/`.

2.6 **Serena project (A5).** Activate Serena on the worktree and commit the generated `.serena/project.yml` with a `.serena/.gitignore` covering its cache. Keep the config minimal but set `languages: [python]` explicitly: a 2026-10-30 activation reported no language servers, and a committed config without a working index turns on `block-grep-code.sh` while symbol tools return nothing, leaving workers with neither. Prove `find_symbol load_agent_prompt` returns `pipeline/runtime.py` before committing. Ignore `obsidian-vault/`. Note in the resolution that the Grep-block hook now enforces in this repo. `.serena/project.yml` is listed in `worktree.tool_managed_paths`, so the OS already expects the file; committing it is compatible.

2.7 **`.claude/settings.local.json` (A7).** It lives only in the user's main checkout and is git-ignored globally; a worker cannot and should not touch it. Resolution is user-action: a one-line suggestion to drop `defaultMode: bypassPermissions` and the stale Notion permissions.

2.8 **Done when:** `load_agent_prompt` loads every prompt filename that `pipeline/cost_forecast.py` and `pipeline/nodes/` reference (pipeline timers run from the dev folder, so the move goes live on the next pull); pipeline tests pass (`pytest tests/` at root, same as CI's pipeline job); `python -c "from pipeline.runtime import load_agent_prompt; load_agent_prompt('kb-curator.md')"` succeeds from repo root; `git grep -n "\.claude/agents"` returns only the test sample string and history/plan docs; CLAUDE.md exists and is under 45 lines; resolution notes under A1-A7.

## 3. Memory consolidation into Jarvis KB (audit section B)

Covers B1-B3. Decision on B3: the Jarvis knowledge base (`jarvis learn`) is the single source of truth for this project. Claude auto-memory for this project is retired.

3.1 **Inputs.** The 33 files in `/home/gonzalo/.claude/projects/-home-gonzalo-workspace-painforwisdom-painforwisdom/memory/` (32 topic files plus `MEMORY.md`) and the existing KB: `jarvis learn list --project painforwisdom`, bodies via `jarvis learn show <id>`.

3.2 **Assess every file.** For each topic file decide one of:
- migrate: still true and still useful. Write it with `jarvis learn add "<text>" --project painforwisdom --topic <topic>`. Reuse existing topics (operations, coach, pipeline, knowledge-base, overview, status) where they fit. Rewrite tersely; keep the "why" and "how to apply" of feedback entries. Convert relative dates to absolute.
- merge: already covered by an existing KB entry; nothing new to add, or the new detail is folded in by adding a superseding entry.
- drop: history, stale, contradicted, or duplicated by user-level config (e.g. "Absolute paths in chat" duplicates `~/.claude/CLAUDE.md`; "blog-post-catchy-title dropped" is moot after 2.3; "Grounding-gate package landed" is history).
Verify facts against the repo and `git log`/`git tag`/`gh pr view` before migrating (e.g. "Coach deploy state" says v0.1.5 but tags reach v0.1.15; Stream 3 PR #66 status). Prod is not readable from a worker (KB kn-775ab565); mark unverifiable prod facts "unverified" rather than guessing.

3.3 **User and feedback memories** (e.g. Max subscription, no silent feature drops, PoC before migration, cost forecast before replay) are guidance for any session here, so they migrate too (project scope, topic `feedback` or `user`). Do not write them into user-level config (1.4).

3.4 **Stale KB entries.** If an existing KB entry is contradicted by what you verified (e.g. kn-e17e2272 "LIVE STATUS SNAPSHOT 2026-10-29"), add the corrected entry. Retire only with a verb `jarvis learn` actually offers; check `jarvis learn --help` first, never edit the KB store directly.

3.5 **Retire auto-memory.** Two steps, both user-action unless the worker's sandbox allows the move:
- stop workers loading and writing it: a `settings_overrides` command that adds the Claude Code setting that disables auto-memory (verify its exact name with the `claude-code-guide` agent; expected `autoMemoryEnabled: false`) while keeping the existing `permissions.deny` list verbatim (1.2: objects are replaced whole). Jarvis's PostToolUse hook mirrors worker memory writes into the KB (`capture_memory_write`), so once auto-memory is off nothing is lost. The mirror is one-way (memory to KB, `capture_memory_write` in Jarvis `src/jarvis/hooks.py`); nothing writes KB back into auto-memory.
- archive, never delete: `mv /home/gonzalo/.claude/projects/-home-gonzalo-workspace-painforwisdom-painforwisdom/memory /home/gonzalo/.claude/projects/-home-gonzalo-workspace-painforwisdom-painforwisdom/memory.archived-2026-10-30`. Do it only after every migrate entry is confirmed in `jarvis learn list`. If the move is denied, give the user the command.
Note for the user: this also removes the memory from their own interactive (non-Jarvis) Claude sessions in this repo; that is the intended outcome.

3.6 **Repo deliverable.** Never edit CLAUDE.md (section 2 owns it). `docs/memory-migration-2026-10-30.md`: one table row per memory file (file, verdict, KB id or reason). Plus the resolution notes under B1-B3 (1.1).

3.7 **Done when:** every one of the 32 topic files has a verdict row; every migrate row names a KB id that `jarvis learn show` returns; the settings_overrides command is in the final message; the memory dir is archived or the `mv` command is handed over.

## 4. Worker harness wiring via Jarvis config (audit section C)

Covers C1-C4. Output is commands for the user (1.2), not catalog edits. Goal: strip from Jarvis worker sessions in this project everything this project never uses, without touching user-level config.

4.1 **Measure first.** From the worker's own session (its system prompt and tool list are the evidence) and `jarvis config wiring painforwisdom --refresh`, list every fixed-cost item: plugins (superpowers, frontend-design, context7, serena, pyright-lsp), skills (`anthropic-skills:*`), MCP servers (claude-in-chrome, easel, claude-artifacts), built-in tools (Artifact, ArtifactComments, ArtifactData, SendUserFile, PushNotification, RemoteTrigger, Cron*, DesignSync, mcp__claude-in-chrome__*). Use `jarvis cost painforwisdom` / `jarvis inspect <wo-id>` to read the session-start context size of recent orders as the baseline.

4.2 **Decide per item.** Keep: serena, pyright-lsp, context7, superpowers (Jarvis workers use brainstorming/TDD/debugging skills; recommend keeping unless you show it unused in recent work orders via `jarvis navigation`/transcripts). Drop: frontend-design (`wiring.disabled_plugins`), every account-level tool and MCP server this project never uses. For each, find the Jarvis mechanism: `wiring.disabled_plugins`, `wiring.disabled_skills`, `worker.denied_tools` (accepts tool names and `mcp__server__*` patterns, as Claude Code `--disallowedTools` does; verify), or `settings_overrides` (`disabledMcpjsonServers`, `enabledPlugins`). Things that reach the session but appear in no wiring list (claude-in-chrome, easel, claude-artifacts, `anthropic-skills:*`) are a Jarvis gap if no key filters them: file it (1.3) and still give the best available command (e.g. `denied_tools`).

`settings_overrides` is one object shared with section 3, which proposes an auto-memory switch in it. If you also propose a `settings_overrides` change, write one combined command carrying the existing `permissions.deny` list, the auto-memory key from 3.5, and your keys, and say it supersedes section 3's command.

4.3 **C1 model/effort.** Already handled for workers (1.5). User-level `opus[1m]`/`high` only affects the user's interactive sessions, which this feature does not touch. Resolution: done, no change. If you find a work-order kind running on a model or effort the catalog does not set for this project (Neo, validation seats, investigations), propose the project-scoped key.

4.4 **C4 autoMode.environment.** User-level; skip unless `settings_overrides` can carry a project-correct `autoMode.environment` for workers, in which case propose the command with an accurate two-line description (monorepo with remote `origin`, coach in docker, vault submodule).

4.5 **Verification of the effect.** A worker cannot apply its own commands, so it cannot measure the saving. State the expected token cut per item from 4.1 and give the user one check to run after applying: `jarvis config wiring painforwisdom --refresh` plus the context-at-first-call of the next work order (`jarvis inspect`).

4.6 **Done when:** resolution notes under C1-C4; every command in the final message is copy-pasteable, carries current members of list/object keys, and has `--reason`; any Jarvis gap has an issue link.

## 5. Jarvis OS findings (audit section D)

Covers D1-D3. Jarvis OS is a separate project; nothing here changes painforwisdom code.

5.1 D1 (per-WO prompt duplicates `jarvis brief`) and D2 (the "say in a few words what you're doing" nudge forces extra turns, each re-reading the whole context) become Jarvis OS issues via the `report-jarvis-bug` skill. Each issue carries the measured cost: for D1 the size of the duplicated prompt sections in characters (measure from your own worker prompt file under `.jarvis/turns/`), for D2 the count of nudge-induced assistant turns in a few recent transcripts under `~/.claude/projects/*painforwisdom*/` and the context size each re-read.

5.2 Before filing, search the tracker for duplicates (`gh issue list --repo <jarvis repo> --search ...`; the skill names the repo). If one exists, comment with the new measurement instead of filing.

5.3 D3: no cost, resolution "skipped, not sent to the model".

5.4 **Done when:** issue links (or duplicate links) in the resolution notes under D1-D3.

## 6. Pipeline runtime on Claude 5 models (audit section E)

Covers E1-E2, scoped to `pipeline/` (nodes, scripts, `runtime.py`, `llm.py`, `cost_forecast.py`). Coach is out of scope (6.4).

6.1 **Model pins (E2).** Replace 4th-gen defaults with 5th-gen: `claude-sonnet-4-6` to `claude-sonnet-5-5`, `claude-opus-4-7` to `claude-opus-5-5`, `claude-haiku-4-5` to `claude-haiku-5-5`. Introduce one module-level constant pair in `pipeline/runtime.py` (e.g. `DEFAULT_MODEL`, `DEEP_MODEL`) and make `pipeline/nodes/{extract,writer,research,youtube_upload,kb_curator}.py` and `pipeline/scripts/{poc_brief,poc_brief_v2,normalize_themes,build_theme_stats,augment_research_tasks}.py` read it; keep the `PIPELINE_MODEL` env override. Also rewrite the `model:` frontmatter in `pipeline/agents/*.md` (pins `claude-opus-4-6`, `claude-sonnet-4-6`, `claude-haiku-4-5`; map `opus-4-6` to `claude-opus-5-5` too). Frontmatter is stripped at load so this is cosmetic, but stale pins mislead readers and fail 6.5. Regenerate `pipeline/forecast.md` (generated output of `cost_forecast.py`) after 6.2, or delete it if nothing reads it. Verify the model IDs resolve through the path the pipeline actually uses (`pipeline/llm.py` via litellm, and `claude -p --model` in `poc_brief_v2.py`/`augment_research_tasks.py`) with one tiny live call each, cheap prompt, on the Max subscription.

6.2 **`cost_forecast.py`.** Add 5th-gen rows to `PRICES` (look up current list prices; cite the source URL in a comment) and switch its stage models and `litellm.token_counter` model to the new constants. Keep the 4th-gen rows only if something still references them.

6.3 **Cache padding (E1).** What it is: Anthropic prompt caching only engages when the cached block is at least a minimum size (the code measured ~2,048 tokens on Sonnet 4.6), so `CACHE_TOKEN_FLOOR` and `CACHE_PADDING_APPENDIX` in `pipeline/runtime.py` exist to push short stage prompts over it. The appendix is NOT filler: it carries the rules that make stages reply inline and not call tools. So: keep the appendix text, rename it (e.g. `EXECUTION_CONTEXT_APPENDIX`), delete `CACHE_TOKEN_FLOOR` and the Sonnet-4.6 padding comments and docstring lines in `load_agent_prompt`. Do not re-measure the cache floor; with the appendix kept, prompts stay the same size.

6.4 **Out of scope, do not touch:** `services/coach/**`. The coach's live grounding detector and judges (`services/coach/eval/grounding/*`, `doctrine/distill.py`) were calibrated on `claude-sonnet-4-6`; switching them needs re-calibration, and the coach's production model pin is in draft PR #78. Filed to backlog by the planner.

6.4a **Tests.** Extend `tests/test_agent_prompts.py` (created by section 2): loaded prompts still contain `EXECUTION CONTEXT` (appendix survived the rename); the default and deep model constants are keys in `cost_forecast.PRICES`.

6.5 **Done when:** `git grep -nE "claude-(sonnet|opus|haiku)-4" pipeline/` returns nothing except PRICES rows kept on purpose; `CACHE_TOKEN_FLOOR` is gone; root `pytest tests/` passes; the live-call evidence (model ID, one-line response, usage) for both paths (litellm and `claude -p`) is in the PR body. If a live call fails for auth reasons (`pipeline/llm.py` needs `ANTHROPIC_AUTH_TOKEN` or the OAuth refresh), record the exact error and hand the user the command; it is not blocking. Resolution notes under E1-E2.

## Agent profile

You are a housekeeping engineer on painforwisdom, a monorepo being revived after months idle. It has three parts: `pipeline/` (LangGraph content pipeline, plain Python, conda env `painforwisdom-poc`, tests in root `tests/`), `services/coach/` (Telegram coach on docker compose and the Claude Agent SDK, tests on host with `PYTHONPATH=services/coach`), and `obsidian-vault/` (a git submodule; never commit inside it). CI is `.github/workflows/ci.yml`; match its commands when you run tests.

Your work order implements one section of the spec `docs/superpowers/specs/2026-10-30-harness-housekeeping.md`, which executes the token audit `docs/token-audit-2026-10-30.md`. Read your audit section's items before starting. Section 1 of the spec applies to you whatever your section is.

What you must know:
- The Jarvis catalog (`/home/gonzalo/workspace/production/config/catalog.json`) is read-only to you. Read it with `jarvis config show|get|wiring`; every change you want is delivered as an exact `jarvis config set painforwisdom <path> <value> --reason "..."` command for the user. `set` replaces list and object values whole: include the current members.
- Never edit user-level Claude config (`~/.claude/settings.json`, `~/.claude/CLAUDE.md`, rules, plugins).
- A missing project-scoped Jarvis setting is a Jarvis bug: file it with your `report-jarvis-bug` skill, then continue.
- The user has a Max subscription: talk tokens and quota, not dollars. Subscription LLM calls from scripts go through `claude -p "..." --output-format json --model <id>`. Keep any live call tiny.
- Worker sessions cannot read coach production (docker on coach containers, `~/workspace/painforwisdom-live`). Do not try; mark such facts unverified.
- `OPERATION.md` in the main checkout is generated by Jarvis; never commit it.
- Durable knowledge goes to `jarvis learn add ... --project painforwisdom`; chat is lost.
- If a command you need (`jarvis learn add`, `gh issue create`, a `mv` outside the worktree) is denied in your session, do not work around it: put the exact command and text in your final message for the user.

Conventions: smallest change that fully solves your section; reuse existing modules; one-line comments for the why only; record every audit item's outcome as a `Resolution (wo-<id>):` sub-bullet under that item in `docs/token-audit-2026-10-30.md`, and touch no other section's items. Your final message lists every command the user must run, verbatim.

Never: delete files outside the repo (archive with `mv` instead), touch a sibling's section, change `services/coach/**` unless your section says so, push to main, or drop an audit item silently. If an item turns out wrong or blocked, say so in its resolution note.
