# Session pipeline v2

A second content pipeline beside the LangGraph one. It has the same stages, the same prompts and the same outputs. Each LLM call runs as a Claude Agent SDK session rather than a LiteLLM completion. A plain-Python orchestrator walks the stages and records every step, session, approval and artifact in SQLite. The point is an A/B test: run both on the same transcript, compare, keep one.

## 1. Context and decisions

- v1 is `pipeline/graph.py` (LangGraph DAG), driven by `pipeline/run.py`. Its five LLM calls all go through `pipeline/llm.py:call_llm`: `pipeline/nodes/extract.py`, `writer.py`, `youtube_metadata.py`, `research.py` (`web_search=True`) and `kb_curator.py` (two call sites). Every other node is deterministic.
- v1 already bills the Max subscription: `call_llm` sends the OAuth token from `~/.claude/.credentials.json` through LiteLLM. v2 changes the transport to real Claude Code sessions. It does not change billing.
- **Transport swap only** (user, via Neo, Q3876). Same system prompt, same user message, same parsers, same appliers. Sessions get no tools except research, which gets `WebSearch` and `WebFetch` in place of the API `web_search` tool. Agentic stages are backlog `bl-930cef66`.
- **Orchestrator is code**, not a Claude session. Retries, joins and approvals must be deterministic, or the A/B measures orchestration luck.
- **v1 behaviour must not change.** Any change to a v1 file must be a no-op when v2 is not running, and the existing tests prove it.
- Package: `pipeline/v2/`. Entry point: `python -m pipeline.v2.run`.
- Out of scope: crash resume from the DB, the `--dir` batch mode (backlog `bl-95696b49`), and launching v2 from the console.

Topology both engines follow (from `pipeline/graph.py:build_graph`):

```
transcribe, extract
extract: kb_curator, extract_image
kb_curator: pre_validator
pre_validator: writer, research
writer: notion_blog, youtube_metadata
youtube_metadata: youtube_upload: bv_youtube
research: notion_research: bv_research
notion_blog + extract_image: wordpress_draft: bv_wordpress
bv_wordpress + bv_research + bv_youtube: summarizer
```

`--from-transcript` starts at `extract` with transcript fields pre-filled (`pipeline/run.py:_build_initial_state`).

## 2. Session runner and LLM backend hook

Files: new `pipeline/v2/session.py`; a small change to `pipeline/llm.py`; `claude-agent-sdk` added to `pipeline/requirements.txt`.

### 2.1 Backend hook in `pipeline/llm.py`

Add a `contextvars.ContextVar` holding an optional backend callable, plus a context manager `llm.backend(fn)` that binds it. `call_llm` checks it first. When a backend is bound, `call_llm` returns `fn(model=..., system_prompt=..., user_message=..., max_tokens=..., web_search=..., tools=...)` unchanged. `long_context`, `max_retries` and `base_backoff_s` are ignored when a backend is bound (no pipeline caller passes `long_context` today). When none is bound, `call_llm` behaves exactly as today, byte for byte. This is the only change to the v1 LLM path.

The backend's return dict must carry the keys every caller reads: `text, duration_s, input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens, cost_usd, billing_mode, model, stop_reason, attempts`.

### 2.2 `run_session`

```python
run_session(system_prompt, user_message, *, model, allowed_tools=(), max_turns, cwd) -> SessionResult
```

`SessionResult` holds `text, session_id, model, input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens, notional_cost_usd, duration_s, num_turns, stop_reason, messages` (the full message log as JSON-able dicts), plus `sdk_version` and `cli_version`.

- Use `claude_agent_sdk.query` with `ClaudeAgentOptions`. Take the pattern from `services/coach/agent/service.py:_build_agent_options`.
- **Isolation.** `setting_sources=[]`, so no CLAUDE.md, hooks, plugins or MCP servers from this machine leak into a session. `cwd` is the run folder. `allowed_tools` is the explicit list; every other tool is denied.
- **Auth.** Strip `ANTHROPIC_API_KEY` from the child environment so the CLI bills the logged-in subscription and never API credits. Do not send the `CLAUDE_CODE_IDENTITY` block from `llm.py`; the CLI is the real identity.
- `max_turns`: 1 for tool-less calls. Research gets a bound high enough for its searches (start at 30 and record the real `num_turns`).
- `max_tokens` has no SDK equivalent. Record the requested value and log a warning when the output exceeds it. Never truncate.
- Usage comes from the SDK `ResultMessage`. Report `cost_usd = 0.0` and `billing_mode = "subscription-session"`. Keep the SDK's `total_cost_usd` as `notional_cost_usd`.
- Retry transient failures (CLI crash, rate limit, connection) with the same backoff shape as `call_llm` (4 retries, base 4 s). Raise others.
- Keep calling `pipeline.usage.record_start/record_end/record_error` so `usage.jsonl` keeps its format (spec 2026-11-03-console-operability §4).

### 2.3 Backend factory

`session_backend(on_session: Callable[[SessionResult, dict], None]) -> callable` builds the backend for 2.1. It maps `web_search=True` to `allowed_tools=("WebSearch", "WebFetch")` and anything else to no tools. It calls `on_session(result, request)` after each session, where `request` is the system prompt, user message, model, tools and max tokens. The orchestrator (section 5) wires `on_session` to the store (section 3). `session.py` never imports the store.

### 2.4 Fake

`FakeSession` (in `session.py` or `tests/`): a scripted `run_session` replacement returning canned `SessionResult`s, so unit tests and CI never need a Claude login.

### 2.5 Unknowns to settle in this section's PR

Run one real session per kind, tool-less and research, and write the answers into the PR body:
1. Does `ResultMessage.usage` report cache read and creation tokens with the same meaning as LiteLLM's? If not, say so, because the A/B token comparison depends on it.
2. Does the research stage's final text still parse through `research._parse_csv` when search runs as `WebSearch` inside a session?

## 3. Audit store

Files: new `pipeline/v2/store.py` and `pipeline/v2/audit.py`.

SQLite, standard library `sqlite3`, WAL mode, one connection per thread. Path from env `PIPELINE_V2_DB`, defaulting to `<PROJECT_ROOT>/pipeline/v2.db` (gitignored). The sandbox profile and dry runs set their own path, the same way `CHECKPOINT_DB_PATH` is handled.

Tables (all timestamps as unix float, all JSON as TEXT):
- `runs(run_id PK, engine, profile, dry_run, input_kind, input_path, input_sha256, language, git_sha, started_at, ended_at, status, verdict, error)`
- `steps(id PK, run_id, node, attempt, started_at, ended_at, status [running|ok|noop|error|paused], error, state_in_keys, state_out JSON)`. `state_out` is the node's return dict with long strings capped as `pipeline/graph.py:end_result` does.
- `sessions(id PK, run_id, step_id, session_id, model, system_prompt, system_prompt_sha256, user_message, user_message_sha256, allowed_tools, max_turns, max_tokens, text, messages JSON, input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens, notional_cost_usd, duration_s, num_turns, stop_reason, sdk_version, cli_version, error)`
- `hitl(id PK, run_id, step_id, kind [approval|error], request JSON, reply, asked_at, answered_at)`
- `artifacts(id PK, run_id, step_id, path, sha256, bytes)`: every `*_path` value a step returns.

API: `Store(path)` with `start_run`, `end_run`, `start_step`, `end_step`, `record_session`, `record_hitl_ask`, `record_hitl_reply`, `record_artifacts`. Each write is its own transaction. Storage errors are logged and never fail the run, the same rule as `pipeline/usage.py:_write`.

Full prompts and the message log are stored on purpose. Reproducibility means a session can be re-sent byte for byte from its row.

`python -m pipeline.v2.audit <run_id>` prints the trail: steps in order with status and duration, sessions with model, tokens and turns, approvals with their replies, and artifacts with their hashes. `--session <id>` prints one session's full prompt and text.

## 4. kb_curator pause seam

File: `pipeline/nodes/kb_curator.py` only.

`node_kb_curator` (`kb_curator.py:572`) calls `langgraph.types.interrupt`, which only works inside a running graph. Split it:

```python
def curate(state: State, ask: Callable[[dict], Optional[str]]) -> Dict[str, Any]: ...  # today's body, interrupt replaced by ask
def node_kb_curator(state: State) -> Dict[str, Any]:
    return curate(state, interrupt)
```

The behaviour of `node_kb_curator` must not change: same payloads, same `plans.json` replay cache, same metrics. `tests/test_real_graph_hitl.py`, `tests/test_kb_curator_apply.py` and `tests/test_kb_curator_unknown_action.py` pass unchanged. Add one test that drives `curate` with a plain function returning `"yes"` and no LangGraph runtime. Note: `tests/test_real_graph_hitl.py` patches `kb_curator.call_llm` directly, so it never exercises the backend hook of section 2.

v2 passes an `ask` that blocks in-process (section 5.4), so the loop in `curate` simply continues on the reply. No replay is needed there.

## 5. Orchestrator and CLI

Files: new `pipeline/v2/run.py` and `pipeline/v2/orchestrator.py`.

### 5.1 CLI and process bootstrap

`python -m pipeline.v2.run` takes `--video`, `--from-transcript`, `--profile prod|sandbox`, `--dry-run`, `--auto-approve`, `--run-id`, `--language`, `--processed-root`, `--reminder-interval` and `--telegram-on-error`, with the same meanings as `pipeline/run.py:main`. There is no `--dir`.

Copy the env bootstrap order of `pipeline/run.py:46-100`: profile env file first, then `dry_run.activate`, and only then import anything that reads paths. That includes `pipeline.graph` (needed for `_instrumented`): it imports every node module and reads `CHECKPOINT_DB_PATH`, so import it after `dry_run.activate`, as `pipeline/run.py:102` does. **Never import `pipeline.run`**: it loads `.env` and parses argv at import.

The run folder is `processed/<run_id>/langgraph/`. That is the console's hardcoded layout (`pipeline/ui/runs.py:62`). `run.json` carries the same fields `pipeline/run.py:_write_run_record` writes, plus `"engine": "sessions"`. The exit code follows `pipeline/run.py:_process_one_video`: 0 on PASS or PARTIAL, 2 on FAIL.

### 5.2 Walking the DAG

- The topology from section 1 is a table in `orchestrator.py`. Nodes are the existing `pipeline.nodes.*` functions, called as `fn(state) -> dict`, registered by name in a dict. kb_curator is `lambda s: curate(s, ask)`.
- A node starts when all its predecessors are done. Ready nodes run in parallel on a `ThreadPoolExecutor`. Joins fire **once**: `wordpress_draft` after both `notion_blog` and `extract_image`, `summarizer` after all three branch validators. (LangGraph fires a join once per inbound edge; record that difference in the A/B notes. The final outputs are the same.)
- State merge: `metrics` and `branch_validations_done` are list reducers (`pipeline/state.py`, `Annotated[..., add]`) and are appended. Every other key overwrites. `node_summarizer` returns `{}` until it has seen all three branches, so it must get the merged list.
- Wrap every node with `pipeline.graph._instrumented(name, fn)` so C1 events (`events.jsonl`) and `usage.bind` behave as in v1. Bind inside the worker thread, because a pool thread does not inherit the parent's contextvars. Also bind the session backend from section 2 inside the worker with `llm.backend(...)`.
- Retry: reuse `pipeline.graph._is_transient` and `_RETRY_POLICY`'s numbers (3 attempts, 2 s initial, x2, 60 s cap, jitter) on the nodes v1 retries (`graph.py:212-218`). Never retry `extract_image`, `youtube_*`, `wordpress_draft` or the validators.
- Every step writes its `steps` row, artifacts and sessions through the store (section 3).

### 5.3 Errors

A node that fails after its retries follows `pipeline/run.py:_drive_graph`: ask over Telegram through `pipeline.hitl.ask` with `hitl.error_request(...)`. On `retry`, re-run that node. On anything else, abort. With `--auto-approve`, re-raise at once, sending to Telegram first when `--telegram-on-error` is set. Record both the question and the reply in `hitl`.

### 5.4 Approvals

kb_curator's `ask` writes `paused` to `events.jsonl` (`_instrumented` only does that for `GraphInterrupt`), sets run status `waiting_hitl`, blocks on `pipeline.hitl.ask(run_dir, hitl.approval_request(...), reminder_interval)`, records the reply, emits `start` again and returns the reply. With `--auto-approve`, behave as `pipeline/run.py:_refuse_unattended_curator` and reply `"yes"`. Net work time excludes human wait, as `hitl_wait_s` does in v1.

### 5.5 Tests

Unit tests with `FakeSession` from section 2.4 and stub nodes: topology order, join fires once, reducers, retry classification, error ask, approval ask, store rows. One test runs the real node table over `--from-transcript` with fake sessions and fake external clients (model it on the `Rig` in `tests/test_real_graph_hitl.py`; if it touches a vault or Notion, mark it integration) and checks that `run.json`, `events.jsonl` and the store agree. Anything needing the real SDK is marked `pytestmark = pytest.mark.integration` and skips when no Claude login is present.

## 6. Console compatibility

Files: `pipeline/ui/actions.py`, `pipeline/ui/runs.py`, and their Python tests only. No JavaScript change.

- Add `b"pipeline.v2.run"` to `RUNNERS`, so Stop recognises a v2 process.
- `runs._record` builds a fixed dict from `run.json`. Pass `engine` through it (default `None`), so actions can see it.
- A run with `engine == "sessions"` gets blockers on Retry, Restart and the dashboard's `manage.rerun` (which uses `_restart_blocker`). The reason reads plainly, e.g. "v2 (sessions) run: relaunch it with python -m pipeline.v2.run". Retry is already blocked by the missing checkpoint, but its current message ("restart it from scratch") is wrong for v2. Restart would relaunch through v1 (`launcher.launch_path`). Use each action's existing blocker status code. Do not invent a new one.
- Reply, Stop, Dismiss, Delete and Log work as they do for v1. Runs with no `engine` are v1, and nothing changes for them. The existing `tests/test_ui_console_*` pass unedited.
- If a change touches decision-tree ids, keep `tests/test_decision_tree.py` green (spec 2026-11-05-console-end-to-end section 2).

## 7. A/B harness

Files: new `pipeline/v2/ab.py`; a section in `OPERATIONS.md`.

`python -m pipeline.v2.ab --from-transcript <f.txt> [--repeat N] [--profile sandbox]`:
- For each repeat, run `python -m pipeline.run --dry-run --auto-approve` and then `python -m pipeline.v2.run --dry-run --auto-approve` as subprocesses, each with its own run id (`ab-<ts>-<n>-v1` and `ab-<ts>-<n>-v2`). Run them **one after the other**, never at the same time: both draw on the same subscription limits, and overlap would skew timing.
- `--auto-approve` refuses an approval that is not a plain yes (`_refuse_unattended_curator`). Treat a refused run as a result and report it; do not hide it.
- Report to `processed/ab/<ts>/report.md` and `report.json`. Per engine: wall time, per-node duration (from `events.jsonl`), LLM call count, tokens by kind (from `usage.jsonl`), retries and errors, final verdict (`run.json`). Outputs side by side: blog title, word count, research row count, themes attached, YouTube title, and the validator findings that differ.
- It compares. It does not judge which output reads better.

`OPERATIONS.md` gains a short section: how to run v2, how to run the A/B, where the DB is, and how to read `pipeline.v2.audit`.

Proof for this section is one real A/B run on a fixture transcript (`tests/fixtures/`) in sandbox profile, with `report.md` pasted into the PR body.

## Agent profile

You are a worker building the session-driven pipeline v2 of the painforwisdom repo, a personal content pipeline. It turns run videos into transcripts, extraction reports, Obsidian vault entries, blog posts, research tasks and YouTube metadata. v2 runs the same stages as the LangGraph pipeline (`pipeline/graph.py`, `pipeline/run.py`) with its LLM calls moved to Claude Agent SDK sessions, a plain-Python orchestrator, and a SQLite audit store. It exists to be A/B tested against v1. Your spec section is the contract. Build exactly that section and nothing beyond it.

What you must know:
- Python, conda env `painforwisdom-poc`. Pipeline tests: `python -m pytest tests -q -rs` from the repo root. A new test file lands in the core CI job by default. A test using a server, a subprocess, the real SDK or an external binary gets `pytestmark = pytest.mark.integration`. CI has no Claude login, so unit tests use the fake session.
- Stage prompts live in `pipeline/agents/*.md` and are loaded by `pipeline/runtime.py:load_agent_prompt`, which strips everything from `## OUTPUT` on; nodes re-add their own `*_OUTPUT_SPEC`. Never edit stage prompts. Same prompts in both engines is the point of the A/B.
- Dry runs (`pipeline/dry_run.py`) change env vars before path-reading modules import. Order of imports at process start matters. Never import `pipeline.run` from v2 code: it loads `.env` and parses argv at import time.
- The console (`pipeline/ui/`) reads `processed/<run_id>/langgraph/{run.json,events.jsonl,usage.jsonl}`. Keep those formats exactly.
- kb_curator writes the real Obsidian vault and Notion. Develop and test with `--profile sandbox` or `--dry-run`, never prod. `obsidian-vault/` is a git submodule: never commit inside it.

Rules:
- v1 must behave exactly as before. Any edit to a v1 file (`pipeline/llm.py`, `pipeline/nodes/kb_curator.py`, `pipeline/ui/actions.py`, `pipeline/ui/runs.py`) must be a no-op for v1, and the existing tests must pass unchanged. If an existing test needs editing, stop and ask Neo.
- Reuse what exists: `pipeline.graph._instrumented`, `_is_transient`, `end_result`, `pipeline.hitl`, `pipeline.usage`, node functions, parsers. Do not fork or copy them into `pipeline/v2/`.
- Sessions run isolated: `setting_sources=[]`, explicit `allowed_tools`, `cwd` set to the run folder, `ANTHROPIC_API_KEY` removed from the child env. Never let a session read or write outside what its section allows.
- Telemetry and audit writes never fail a run.
- Work in your worktree, open a PR, and use the repo's PR template. Never merge it yourself without the gate, and never push to main.
- Stay inside your section. If you need something another section owns, such as the store API or the backend hook, use the interface written in the spec. If that interface is wrong, ask Neo rather than changing it in your PR.

Traps the planner already found:
- LangGraph fires a join node once per inbound edge; v2 fires it once. Summarizer and wordpress_draft cope with both. Do not "fix" either node.
- `_instrumented` emits `paused` only on `GraphInterrupt`. A v2 approval must emit it itself.
- `_ATTEMPTS` in `pipeline/graph.py` is process memory, keyed by `(run_dir, node)`.
- The SDK has no `max_tokens`. Never truncate output to imitate it.
- A pool thread does not inherit contextvars. Bind `usage` and the LLM backend inside the worker.
