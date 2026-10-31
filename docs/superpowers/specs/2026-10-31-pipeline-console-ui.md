# Pipeline console UI (2026-10-31)

Feature order `fo-13add196`. A local, visual web console to operate the content
pipeline (`pipeline/graph.py`, `pipeline/run.py`): start a run with an input picked
in a popup, watch the DAG animate as steps execute, inspect every intermediate
artifact, read the final result fetched from Notion through a new backend read
path, and see telemetry (total time, time per step, tokens, cost).

Processing stays as it is. The only pipeline changes are observability (section 2)
and recording Notion research page ids (section 4). No node logic, prompt, model,
or persistence target changes.

## 1. Architecture (reference; no work order)

```
browser (vanilla ES modules + SVG, no bundler)
   │  fetch JSON, poll every 1s while a run is active
   ▼
pipeline/ui/  FastAPI app, `python -m pipeline.ui` on 127.0.0.1:8820
   ├── reads   <processed_root>/<run_id>/langgraph/{run.json,events.jsonl,runs.jsonl,<agent dirs>}
   ├── lists   <inbox>/ (videos + transcripts)
   ├── spawns  [sys.executable, "-m", "pipeline.run", ...]  (or the fake runner in tests)
   └── reads   Notion via pipeline/notion_client.py (or JSON fixtures in tests)
```

- Single user, localhost only, no auth. Bind 127.0.0.1, never 0.0.0.0.
- One active run at a time (Whisper GPU, shared checkpoint DB).
- HITL approvals and error-recovery replies keep going through Telegram. The UI shows
  the waiting state; it does not answer. (Answering from the UI is backlogged.)
- Test strategy: pytest for every Python module (`tests/test_ui_*.py`, run by the
  existing CI pipeline job) and Playwright (Node `@playwright/test`, chromium) for
  every browser behaviour, against the real FastAPI server over fixture run trees
  and a fake runner. 100% line, function and branch coverage of `pipeline/ui/static/js/`
  measured from Chromium V8 coverage, enforced in CI. A final real sandbox run
  through the UI proves the integration (section 10).
- Playwright was verified on this host 2026-10-31: `@playwright/test@1.64.1` with
  chromium build 1248 passed a probe test.

Dependency order: 2 and 3 first, in parallel. After 3: 4 and 5 in parallel.
6 after 5 (its live specs launch fake runs). Then 7 (needs 6), 8 (needs 4, 6),
9 (needs 6) in parallel. Section 10 needs everything.

## 2. Run observability in the pipeline

Make a run's live progress observable on disk. Files and formats are in the Agent
profile, "Contracts" C1 and C2. Owner of `pipeline/graph.py` and `pipeline/run.py`
for this feature.

1. **Node lifecycle events.** In `build_graph` wrap every node function before
   `g.add_node` with `_instrumented(name, fn)`. The wrapper appends to
   `<state["run_dir"]>/events.jsonl`:
   - `start` before calling `fn`, with `attempt` = how many times this node has
     started in this process for this run_dir (RetryPolicy re-calls the wrapper).
   - `end` after it returns, with `duration_s`, `noop` (true when the returned dict
     is empty: summarizer's early fires and branch-validator re-fires), and
     `outputs` = every key of the returned dict ending in `_path` with a non-empty
     string value.
   - `paused` when `fn` raises `langgraph.errors.GraphInterrupt` (kb_curator HITL),
     then re-raise unchanged. On resume kb_curator restarts from its top, so a
     normal HITL sequence is `start, paused, start, end`.
   - `error` with `error` = `"<ExcType>: <msg>"` truncated to 500 chars, then re-raise
     unchanged (RetryPolicy and run.py must see the original exception).
   - Writes are thread-safe: a module-level `threading.Lock`, one `write()` of one
     full line per event, file opened in append mode. Parallel branches run in a
     thread pool.
   - Use `functools.wraps`. Every node is `def node_x(state)`; keep it that way.
   - A missing/empty `run_dir` in state means no event is written (tests that build
     the graph without a run dir must keep working).
2. **Run record.** `run.py` writes `<run_dir>/run.json` (C2) atomically (write a
   sibling tmp file, then `os.replace`):
   - in `_process_one_video` before `_drive_graph`: `status=running`, `pid=os.getpid()`,
     `profile`, `input`, `started_at`;
   - inside `_drive_graph`: `waiting_hitl` around `_ask_indefinitely`, `waiting_reply`
     around `_ask_bounded`, back to `running` after the reply;
   - on return: `done` with `verdict` (run.py's `"?"` fallback is written as
     `null`), `ended_at`, `hitl_wait_s`; on any exception
     leaving `_process_one_video`: `failed` with `error` (same truncation), then re-raise.
   - Under `--auto-approve` the HITL path never waits, so no `waiting_*` status.
3. **Run id from outside.** `--run-id` already exists; for `--video` and
   `--from-transcript` the given id must be used verbatim (it is today; keep it and
   cover it with a test) because the UI server generates the id and returns it
   before the run directory exists.
4. Tests (`tests/test_run_observability.py`): build a tiny StateGraph using the
   real `_instrumented` wrapper with stub nodes covering success, `{}` noop,
   transient error retried by a RetryPolicy, persistent error, and an `interrupt()`
   resumed with `Command(resume=...)`; assert the exact event sequences. Drive
   `run._drive_graph`/`_process_one_video` with a stub graph and stubbed Telegram to
   assert the run.json status transitions, including failure. No network, no LLM.

Does not touch `pipeline/ui/`, any node module, or `pipeline/state.py`.

## 3. Read API and fixture runs

Create the `pipeline/ui/` package and the read side of the HTTP API (C3: graph,
runs list, run detail, artifacts). Owner of the app factory, the mapping module
and the shared fixtures.

1. `pipeline/ui/__init__.py`, `pipeline/ui/app.py` with `create_app(settings) -> FastAPI`,
   `pipeline/ui/settings.py` (`Settings` dataclass: `processed_root`, `inbox`,
   `runner`, `notion_fixtures`, `profile`, `host`, `port`; `Settings.from_env_and_args`),
   `pipeline/ui/__main__.py` (`python -m pipeline.ui [--port] [--processed-root]
   [--inbox] [--profile]`, runs uvicorn). Env names and defaults are in C5.
2. Routers, one module each, each exposing `router = APIRouter()`; `create_app`
   includes them all: `routes_graph.py`, `routes_runs.py` (this section), and empty
   placeholder routers `routes_launch.py` (section 5) and `routes_result.py`
   (section 4) so later sections only fill their own file. `create_app` also mounts
   `pipeline/ui/static/` at `/static` and serves `static/index.html` at `/`
   (a placeholder page until section 6 replaces it), and `GET /health`.
3. `pipeline/ui/mapping.py`: `NODE_TO_STAGE` (graph node name to runs.jsonl
   `stage`, e.g. `extract_image`→`extract-image`, `kb_curator`→`kb-curator`,
   `notion_blog`→`notion-blog`, `wordpress_draft`→`wordpress-draft`, `extract`→
   `extract`, `research`→`research`, `summarizer`→`summarizer`; nodes that log
   nothing map to `None`; plus `LEGACY_STAGE_ALIASES = {"validator": "summarizer"}`
   because 2026-05 runs logged several `validator` rows instead), `NODE_TO_DIR` (node to its
   artifact subdir: `extract`→`coaching-thought-extractor`, `kb_curator`→`kb-curator`,
   `writer`→`painforwisdom-writer`, `research`→`research-curator`, `notion_blog`→
   `notion-blog-post-logger`, `notion_research`→`notion-research-logger`,
   `extract_image` and `wordpress_draft`→`wordpress-draft`, `youtube_upload`→
   `youtube-upload`, `summarizer`→`validator` and `pipeline-summary`; others none),
   `NODE_ICON` (node to an icon key). A pytest asserts every node of
   `build_graph()[0].get_graph().nodes` (minus `__start__`/`__end__`) has an entry in
   all three maps; point `CHECKPOINT_DB_PATH` at a tmp file in that test.
4. `GET /api/graph` derives nodes and edges from the compiled graph once at startup
   (start_at="transcribe"), so topology is never duplicated in JS.
5. `pipeline/ui/runs.py` (pure functions, no FastAPI): list runs, load one run,
   derive node states from events (last event per node wins; `noop` ends do not
   move a node out of `done`; a node never started is `pending`; when the run is
   finished, `transcribe` without events on a transcript input is `skipped`),
   status from run.json (`running`/`waiting_*` with a dead pid reports `crashed`,
   checked with `os.kill(pid, 0)`), and telemetry from runs.jsonl via `NODE_TO_STAGE`.
   Telemetry rules: tokens missing on a row count as 0; the `kb-curator` row has no
   top-level tokens, sum them from `per_call` (same field names as other rows),
   cost from `total_cost_usd`; several rows for one node sum. `work_s` = wall time
   minus `hitl_wait_s`.
   Legacy runs (no run.json, no events.jsonl: every run before this feature) still
   list and open: status `done`, verdict from the last `summarizer` or `validator`
   row, node states
   and durations from runs.jsonl alone, `started_at`/`ended_at` from the first/last
   row `ts`.
6. Artifacts: list every file under the run dir's agent subdirs plus every path in
   `end.outputs` that lies outside the run dir (transcript, vault entry). Each gets
   an opaque `id` (C3). Serve content only for ids in that list; reject anything
   else with 404, never resolve a client-supplied path. Cap served text at 2 MB.
7. Fixture trees (C6), used by every later section: write them by hand to match
   C1/C2 exactly, plus a legacy run copied in shape from a real 2026-05 run.
8. Add `fastapi`, `uvicorn` to `pipeline/requirements.txt` (pinned ranges like the
   others) and `httpx` is already there for `TestClient`. Tests:
   `tests/test_ui_runs.py`, `tests/test_ui_api_read.py` over the fixtures.

Does not spawn processes, does not read Notion, does not touch `pipeline/run.py`,
`pipeline/graph.py` or nodes. Built from the C1/C2 formats alone, in parallel with
section 2.

## 4. Notion result read path

Backend functionality to fetch a run's final result from the persistence layer
(Notion today) so the UI never sends the user to Notion. Fill
`pipeline/ui/routes_result.py`; add `pipeline/ui/result.py`.

1. Record research page ids: in `pipeline/nodes/notion_research.py` add
   `page_ids` (list of the created page ids, in creation order) to the existing
   `append_metric(... "notion-research" ...)` row. Nothing else in that node changes.
2. `pipeline/ui/result.py`: `ResultSource` protocol with `blog(page_id) -> dict` and
   `research(page_ids) -> list[dict]`; `NotionResultSource` using
   `pipeline.notion_client.get_blog_page`, `get_blog_page_markdown`, `page_url`,
   `extract_property` (one `client.pages.retrieve` per research id; property names
   are those `create_research_task` writes); `FixtureResultSource(dir)` reading
   `<dir>/blog/<page_id>.json` and `<dir>/research/<page_id>.json`. Settings choose:
   `notion_fixtures` set means fixtures, else Notion.
3. `GET /api/runs/{run_id}/result` (C3). Blog page id comes from the run's
   `notion-blog` runs.jsonl row `page_id`; research ids from `notion-research`
   `page_ids` (absent on legacy runs: return `research: null`, not an error).
   Status `pending` while the run has not reached those nodes, `ready` when fetched,
   `unavailable` with a short `reason` on any Notion error (never a 5xx, never a
   traceback in the body). Cache a `ready` result in memory per run id.
4. Importing `pipeline.notion_client` reads Notion ids from env at import time:
   import it lazily inside `NotionResultSource`, so the server starts and the test
   suite runs with no Notion env.
5. Fixtures: `tests/fixtures/ui/notion/` matching the `done` fixture run's page ids
   (C6). Tests `tests/test_ui_result.py` (fixture source + a mocked
   `notion_client`, including the error path) and a test that the notion_research
   metric row carries `page_ids` (no such node test exists yet: call
   `node_notion_research` with `create_research_task`, `page_id` and
   `fetch_page_blocks` monkeypatched in that module). Tests that need a live key skip with a reason,
   like the rest of the suite.

Does not touch the UI static files or other routers.

## 5. Launch API and fake runner

Starting a run from the HTTP API, and the deterministic fake runner every browser
test uses. Fill `pipeline/ui/routes_launch.py`; add `pipeline/ui/launcher.py`,
`pipeline/ui/fake_runner.py`.

1. `GET /api/inputs`: files directly in `settings.inbox` with a video extension
   (`pipeline.run.VIDEO_EXTENSIONS`; copy the tuple into `pipeline/ui/launcher.py`
   rather than importing `pipeline.run`, which calls `load_dotenv` at import) or
   `.txt` (transcript). Sorted newest mtime first. Missing inbox dir means an empty
   list, not an error.
2. `POST /api/runs` body `{"input": "<file name>", "auto_approve": false,
   "fake": {"scenario": "ok"|"hitl"|"fail", "step_ms": <int>}}` (`fake` optional,
   accepted only when `settings.runner == "fake"`, else 400; defaults `ok`/200):
   - `input` must equal a name returned by the listing (no paths, no `..`), else 404;
   - 409 when a run whose status is `running`/`waiting_*` and whose pid is alive
     exists (reuse `pipeline/ui/runs.py`);
   - generate `run_id` = `time.strftime("%Y-%m-%d_%H%M%S")` plus `_ui`, unique
     against existing dirs;
   - spawn detached (`start_new_session=True`) with `cwd` = the checkout root,
     stdout+stderr to `<processed_root>/<run_id>/langgraph/stdout.log`:
     real: `[sys.executable, "-m", "pipeline.run", "--profile", settings.profile,
     "--run-id", run_id, "--video"|"--from-transcript", <abs path>]` plus
     `--auto-approve` only when asked; fake: `[sys.executable, "-m",
     "pipeline.ui.fake_runner", ...]`;
   - the child env is `os.environ` as the server received it. The server never calls
     `load_dotenv`: `run.py` loads the profile's env file itself, and a server that
     preloaded prod `.env` would leak prod Notion ids into a sandbox child, because
     `load_dotenv` does not override variables already set;
   - write a provisional `run.json` (`status=running`, child pid) before returning
     201 `{"run_id": ...}`, so the run is listable at once; run.py overwrites it.
   - real runs need `run.py` to accept the processed root: today it writes to
     `PROJECT_ROOT/processed`. The server's default `processed_root` is
     `pipeline.runtime.PROCESSED_ROOT`, the same path, so no run.py change. Document
     that `--processed-root` only affects reads and the fake runner.
3. `pipeline/ui/fake_runner.py` (`python -m pipeline.ui.fake_runner --run-id
   --processed-root --input --scenario ok|hitl|fail --step-ms N`): walks the real
   topology from `build_graph` (no LLM, no Notion, no Whisper) in dependency order,
   running parallel siblings concurrently, writing C1 events, C2 run.json, C4
   runs.jsonl rows with plausible tokens, and small artifact files in the
   `NODE_TO_DIR` subdirs, sleeping `step-ms` per node. `hitl` pauses kb_curator for
   3 steps (`waiting_hitl`) then resumes; `fail` errors at `research`. Notion page
   ids it writes match the fixture Notion files (C6). Scenario and step time come
   from the POST body's `fake` field, passed as CLI args, so one server serves
   every scenario in one suite.
4. Tests `tests/test_ui_launch.py`: listing, validation (404/409), argv building for
   both kinds and both runners (assert the list, do not spawn the real pipeline),
   and one real spawn of the fake runner to completion asserting the on-disk files.

Does not touch static files, `pipeline/run.py`, or other routers.

## 6. Console shell, DAG view and Playwright harness

The frontend skeleton every UI section plugs into, the animated DAG, the run list,
and the browser test harness with its coverage gate and CI job.

1. Static layout (owned here; later sections only edit their own module):
   `pipeline/ui/static/index.html`, `css/app.css`, `js/app.js` (boot, polling,
   bus), `js/api.js` (one function per C3 endpoint), `js/icons.js` (inline SVG
   icons by `NODE_ICON` key), `js/dag.js`, `js/runs.js`, and stub modules
   `js/launch.js`, `js/drawer.js`, `js/telemetry.js` each exporting a no-op
   `mount(el, ctx)`, and empty `css/launch.css`, `css/drawer.css`,
   `css/telemetry.css` linked from `index.html`. No inline `<script>` or inline
   event handlers in `index.html` (everything must sit under the coverage gate).
   `app.js` owns the offline/error badge and shows it on the bus event `ui:error`.
   Module contract in C7.
2. DAG: SVG, columns by longest-path depth from `GET /api/graph`, nodes as icon
   discs, no visible text except a tooltip/`aria-label` with the node label.
   States (C3) map to visuals: pending dim; running a pulsing ring plus animated
   flowing dashes on its inbound edges; paused amber breathing; done green with a
   check; error red with a short shake; skipped hollow. Transitions animate
   (CSS transitions/keyframes, no animation library). Honour
   `prefers-reduced-motion` (no motion, state still visible). Clicking a node emits
   `node:selected`.
3. Run list: a vertical strip of status dots (newest first), live run on top with a
   spinner; clicking selects the run (`run:selected`, URL hash `#/runs/<id>`, so a
   reload restores the selection). Empty state is an icon, not a paragraph.
4. Polling: every 1000 ms while the selected run is `running`/`waiting_*`; stop when
   it ends; the list refreshes every 5 s. Backend errors show a small offline badge
   and polling continues.
5. Harness in `pipeline/ui/e2e/`: `package.json` + `package-lock.json` pinning
   `@playwright/test` 1.64.1 and the coverage tooling (Chromium `page.coverage`
   through a shared fixture, reported with `monocart-coverage-reports` or
   `v8-to-istanbul`; the worker proves whichever it picks reports per-file
   lines/functions/branches), `playwright.config.js` with a `webServer` that copies
   C6 fixtures to a fresh tmp dir and starts `python -m pipeline.ui` with C5 test
   env (fake runner, Notion fixtures, tmp processed root and inbox, a free port).
   `npm test` runs every spec and FAILS when any file under `pipeline/ui/static/js/`
   is below 100% lines, functions or branches. `workers: 1` and
   `fullyParallel: false`: one active run at a time makes live specs serial.
   Shared helpers in `e2e/fixtures.js`, used by every spec:
   - coverage collection per page;
   - fail the test on any `pageerror`, `console.error`, or failed request that the
     spec did not intercept on purpose;
   - `freshServerState({live = false} = {})`: kill every fake-runner pid recorded
     in a `*_ui` run.json under the tmp processed root, wipe that root, re-copy C6
     into it, and only when `live` rewrite the `pid` of `fx-running` and `fx-hitl`
     to the Playwright process pid (so they are live and `POST /api/runs` returns
     409); with `live = false` they keep pid 0 and read as `crashed`, so launch
     specs are not blocked. The server reads disk per request, so no reset
     endpoint is needed.
   Specs live in `e2e/tests/*.spec.js`; `real.spec.js` (section 10) is excluded
   from `npm test` and the gate by `testIgnore`.
6. CI: new `ui` job in `.github/workflows/ci.yml`: Python 3.11 + `pip install -r
   pipeline/requirements.txt`, Node 22, `npm ci` and
   `npx playwright install --with-deps chromium` in `pipeline/ui/e2e`, `npm test`.
   Upload the HTML report as an artifact on failure.
7. Specs for this section: DAG renders every node of the graph fixture; each node
   state renders its visual (one fixture run per state) and sets `data-state`;
   a `running` node's computed `animation-name` is not `none`, and is `none` under
   `page.emulateMedia({reducedMotion: 'reduce'})`; run list
   order and selection; hash restore; polling start/stop (fake runner run via
   `POST /api/runs` from the test, through the API, since the launch UI is section 7);
   offline badge (route intercept). Screenshot of each state for the PR.

Does not implement the picker, drawer or telemetry (stubs only) and does not change
any Python module; it replaces section 3's placeholder `index.html`.

## 7. Launch popup

Start a run from the UI with the input chosen in a popup. Owns `js/launch.js` and
`e2e/tests/launch.spec.js`.

1. A floating round "play" button opens a modal listing `GET /api/inputs` as tiles:
   video or transcript icon, file name in small type, relative age. Keyboard:
   arrows move, Enter starts, Esc closes; focus trapped while open.
2. Choosing a tile posts `POST /api/runs`; on 201 the modal closes with an
   animation and the new run is selected and live. 409: the button shows a busy
   state while a run is active (disabled, spinner) and the modal shows a lock icon.
   404/5xx: shake the tile and show the offline/error badge.
3. An "auto-approve" toggle, off by default, rendered as an icon switch with a
   tooltip; it maps to `auto_approve`.
4. Empty inbox: an empty-tray icon plus the inbox path in small type (the only text).
5. Specs: open/close (click, Esc), keyboard selection, start a fake `ok` run and
   see the DAG go live to done, 409 lockout while a run is live, 404 path, empty
   inbox, toggle sent in the body (assert the request). Coverage of `launch.js`
   100%.

Does not touch other JS modules except the one `mount` call already in `app.js`.

## 8. Step inspector and final result

Inspect any step's intermediate artifacts and the run's final result from Notion.
Owns `js/drawer.js`, `css` additions scoped under `.drawer`, and
`e2e/tests/inspect.spec.js`.

1. `node:selected` opens a side drawer for that node: icon, state, duration, and
   its artifacts (C3 artifacts filtered by `node`). Renderers by kind: markdown
   rendered to safe HTML (escape first; headings, bold, lists, links only; no raw
   HTML pass-through), CSV as a table, JSON pretty, images inline, text in a
   pre. Artifacts of a running node appear as soon as they exist (poll while open).
2. A "result" control (trophy icon) in the header opens the final result view from
   `GET /api/runs/{id}/result`: the blog post rendered from markdown with its title,
   and research tasks as cards with type icons and links. `pending` shows a
   waiting animation and polls; `unavailable` shows a cloud-off icon with the
   reason in small type; `research: null` hides the cards.
3. Links open in a new tab with `rel="noopener"`.
4. Specs: every artifact kind renders (fixture run), XSS payload in an artifact
   is shown as text, drawer follows a live fake run, result ready/pending/
   unavailable (pending from a live fake run, unavailable via a Playwright
   `page.route` intercept of the result endpoint), legacy run with no research ids. Coverage of `drawer.js` 100%.

Does not change Python routes; if an endpoint shape is wrong, fix it in the owning
module (`pipeline/ui/runs.py` or `result.py`) with its pytest, minimally.

## 9. Telemetry panel

Visual telemetry of a run. Owns `js/telemetry.js`, `css` under `.telemetry`,
`e2e/tests/telemetry.spec.js`.

1. A header strip: total wall time as a ticking clock while live, work time, total
   tokens (input/output/cache split as a stacked bar), cost (hidden when every row
   is 0, which is the case on subscription billing).
2. A timeline (Gantt) under the DAG: one bar per node from its first `start` to
   last `end`, coloured by state, parallel branches visibly overlapping, HITL pause
   hatched; hovering a bar highlights the DAG node and shows duration/tokens/model
   in a tooltip. Bars grow live during a run.
3. Numbers format compactly (`1.2k`, `3m 05s`).
4. Specs: totals match the `done` fixture's expected numbers (compute them in the
   spec from the fixture files, not hard-coded), legacy run renders from runs.jsonl
   alone, live growth on a fake run, hover linking, cost hidden at 0. Coverage of
   `telemetry.js` 100%.

Does not change Python modules.

## 10. Real end-to-end acceptance and docs

Prove the console operates the real pipeline end to end on the sandbox profile,
fix what breaks, and document operation.

1. Environment: the worktree has no `.env.sandbox` (it is gitignored and lives in
   the dev checkout `~/workspace/painforwisdom/painforwisdom`). Symlink it into the
   worktree root for the run; never commit it. Check `.env.sandbox` points
   `VAULT_PATH` and `CHECKPOINT_DB_PATH` at sandbox paths before the first run.
   Run `bash tests/sandbox_reset.sh` before each run. Python is the conda env
   `painforwisdom-poc` (`/home/gonzalo/miniconda3/envs/painforwisdom-poc/bin/python`).
   Before the first run confirm `youtube_upload` and `wordpress_draft` are
   dormant or point at sandbox targets under `.env.sandbox`; if either would
   publish publicly, stop and ask.
2. Start `python -m pipeline.ui --profile sandbox --inbox <tmp inbox>`; put
   `tests/fixtures/transcript_2026-04-14.txt` and one short real video copied from a
   `processed/*/source/*.mp4` in the dev checkout into the inbox.
3. `e2e/tests/real.spec.js`, tagged `@real` and excluded from `npm test` and CI
   (opt-in `npm run test:real`, no coverage gate): through the UI only, start the
   transcript run with the auto-approve toggle ON (a HITL approval would otherwise
   block on Telegram), wait for done with verdict PASS (PARTIAL or FAIL fails the
   test; dormant/skipped youtube and wordpress already count as PASS checks in
   their branch validators), open every node with artifacts and assert each
   renders, open the result and assert the blog title equals the title read
   directly with `pipeline.notion_client.get_blog_page` from a small Python helper
   (not through the UI), assert telemetry tokens > 0, and assert no browser
   console errors. `transcribe` shows `skipped`. Repeat for the video run, where
   `transcribe` must show `done`. Timeouts sized for real LLM calls (minutes).
4. Fix every defect found, in the module that owns it, each with a regression test
   (pytest or a non-real Playwright spec). Do not weaken the coverage gate.
5. Docs: a "Pipeline console" section in `OPERATIONS.md` (start command, env,
   inbox, ports, HITL still via Telegram, how to run `npm test` and `test:real`), a
   pointer line in `README.md`, and the console in `CLAUDE.md`'s Components and
   Tests lists (pointers only, CLAUDE.md copies no detail).
6. PR evidence: the `test:real` output, both run ids, screenshots of each run at
   done, the result view, and telemetry.

Does not change pipeline node logic unless a defect requires it; such a fix gets
its own commit naming the defect.

## Agent profile

You are a worker on the painforwisdom **pipeline console UI** feature (spec
`docs/superpowers/specs/2026-10-31-pipeline-console-ui.md`). You implement exactly
one numbered section of that spec, the one you were handed, in TDD: write the
failing test first (pytest or Playwright), then the code, then refactor. You finish
with one pull request.

### Codebase you are in
- Monorepo `gonandrap/painforwisdom`. The content pipeline is a LangGraph DAG in
  `pipeline/graph.py` (`build_graph(start_at)` returns `(compiled, saver)`), driven
  by `pipeline/run.py` (`_drive_graph`, `_process_one_video`). Nodes live in
  `pipeline/nodes/`, are `def node_x(state) -> dict`, and write per-stage telemetry
  with `pipeline.runtime.append_metric` to `<run_dir>/runs.jsonl`. `run_dir` is
  `processed/<run_id>/langgraph/`.
- Python env: conda `painforwisdom-poc`, interpreter
  `/home/gonzalo/miniconda3/envs/painforwisdom-poc/bin/python` (`conda` is not on
  PATH). If `fastapi`/`uvicorn` are missing there, install them with that env's pip
  from `pipeline/requirements.txt`.
- Pipeline tests: `python -m pytest tests -q -rs` from the repo root. Browser tests:
  `cd pipeline/ui/e2e && npm ci && npx playwright install chromium && npm test`.
  CI is `.github/workflows/ci.yml`; it has no secrets, so anything needing Notion,
  OpenAI or Anthropic must skip with a reason or use fixtures.
- `pipeline/run.py` calls `load_dotenv` at import, and several modules
  (`pipeline/runtime.py` `VAULT_PATH`, `pipeline/notion_client.py` DB ids,
  `pipeline/graph.py` `CHECKPOINT_DB`) read env at IMPORT time. Never import
  `pipeline.run` from `pipeline/ui/`; import `pipeline.notion_client` lazily; set
  `CHECKPOINT_DB_PATH` to a tmp file in any test that calls `build_graph`.
- `obsidian-vault/` is a git submodule: never commit inside it. Never commit
  `.env*`, `processed/`, `node_modules/`, Playwright reports or coverage output
  (add ignores under `pipeline/ui/e2e/` as needed).

### Contracts (shared by every section; do not change a contract in a section that does not own it)
- **C1 `events.jsonl`** (owner: section 2), one JSON object per line in
  `<run_dir>/events.jsonl`: `{"ts": <epoch float>, "node": "<graph node name>",
  "event": "start"|"end"|"error"|"paused", "attempt": <int, starts at 1>,
  "duration_s": <float, end only>, "noop": <bool, end only>, "outputs": {<state
  key>: <path>} (end only), "error": "<ExcType>: <msg>" (error only)}`. The state
  of a node is its last event.
- **C2 `run.json`** (owner: section 2), `<run_dir>/run.json`, replaced atomically:
  `{"run_id", "status": "running"|"waiting_hitl"|"waiting_reply"|"done"|"failed",
  "verdict": "PASS"|"PARTIAL"|"FAIL"|null, "pid": <int>, "profile":
  "prod"|"sandbox", "input": {"kind": "video"|"transcript", "path": "<abs>"},
  "started_at": <epoch>, "ended_at": <epoch|null>, "hitl_wait_s": <float>,
  "error": <str|null>}`. The API adds the derived status `crashed`.
- **C3 HTTP API** (owners: section 3 graph/runs/artifacts, section 4 result,
  section 5 inputs/launch). All JSON.
  - `GET /api/graph` → `{"nodes": [{"id", "label", "icon"}], "edges": [{"from", "to"}]}`
    (no `__start__`/`__end__`).
  - `GET /api/runs` → `{"runs": [{"run_id", "status", "verdict", "started_at",
    "ended_at", "input": {"kind", "name"}, "total_s"}]}`, newest first.
  - `GET /api/runs/{id}` → `{"run_id", "status", "verdict", "profile", "input",
    "started_at", "ended_at", "hitl_wait_s", "error", "nodes": {"<node>": {"state":
    "pending"|"running"|"paused"|"done"|"error"|"skipped", "started_at",
    "ended_at", "duration_s", "attempts", "error"}}, "telemetry": {"total_s",
    "work_s", "tokens": {"input", "output", "cache_read", "cache_creation"},
    "cost_usd", "per_node": {"<node>": {"duration_s", "input_tokens",
    "output_tokens", "cache_read_tokens", "cache_creation_tokens", "cost_usd",
    "model"}}}}`. 404 for an unknown id.
  - `GET /api/runs/{id}/artifacts` → `{"artifacts": [{"id", "node", "name",
    "kind": "markdown"|"csv"|"json"|"text"|"image"|"other", "bytes"}]}`; `id` is
    opaque to the client. `GET /api/runs/{id}/artifacts/{artifact_id}` → the file
    with its content type; 404 for any id not in the listing.
  - `GET /api/runs/{id}/result` → `{"status": "ready"|"pending"|"unavailable",
    "reason": <str|null>, "blog": {"title", "url", "markdown"}|null, "research":
    [{"title", "type", "url"}]|null}`.
  - `GET /api/inputs` → `{"inbox": "<abs>", "inputs": [{"name", "kind":
    "video"|"transcript", "bytes", "mtime"}]}`.
  - `POST /api/runs` `{"input": "<name>", "auto_approve": false, "fake":
    {"scenario", "step_ms"} (optional, fake runner only)}` → 201 `{"run_id"}`;
    400 `fake` with the real runner; 404 unknown input; 409 `{"detail": "run
    active", "run_id"}`.
- **C4 `runs.jsonl`** is the existing per-stage telemetry; read it, never rewrite
  existing fields. Only section 4 adds `page_ids` to the `notion-research` row.
- **C5 settings** (owner: section 3): CLI flags override env. `PIPELINE_UI_PORT`
  (8820), `PIPELINE_UI_PROCESSED_ROOT` (default `pipeline.runtime.PROCESSED_ROOT`),
  `PIPELINE_UI_INBOX` (default `pipeline.runtime.STATE_ROOT / "inbox"`),
  `PIPELINE_UI_PROFILE` (`sandbox`|`prod`, default `sandbox`; prod must be chosen
  explicitly), `PIPELINE_UI_RUNNER` (`real`|`fake`, default `real`),
  `PIPELINE_UI_NOTION_FIXTURES` (dir; set means fixture result source). Host is
  always 127.0.0.1.
- **C6 fixtures** (owner: section 3, extended by 4 and 5): `tests/fixtures/ui/processed/`
  with runs `fx-done` (transcript input, verdict PASS, every node done, HITL
  pause on kb_curator, tokens on extract/kb_curator/writer/research, notion-blog
  `page_id` `fx-blog-1`, notion-research `page_ids` `["fx-res-1","fx-res-2"]`, one
  artifact of each kind), `fx-running` (status `running`, extract running,
  later nodes pending; its `pid` is the placeholder `0`, which section 6's harness
  rewrites at copy time to the Playwright runner's own pid so it is alive for the
  whole suite, and pytest rewrites to `os.getpid()` in a tmp copy), `fx-hitl`
  (`waiting_hitl`, same pid rewrite), `fx-failed` (error at `research`), `fx-crashed` (`running` with
  a dead pid), `fx-legacy` (only runs.jsonl and agent dirs, shaped like a real
  2026-05 run); `tests/fixtures/ui/inbox/` with one `.txt` and one tiny `.mp4`;
  `tests/fixtures/ui/notion/` (section 4) with `blog/fx-blog-1.json` and
  `research/fx-res-*.json`.
- **C7 frontend modules** (owner: section 6): ES modules, no bundler, no framework,
  no CDN at runtime. Each feature module exports `mount(el, ctx)`, where `ctx =
  {api, bus, state}`: `api` is `js/api.js`, `bus` has `on(event, fn)`/`emit(event,
  payload)` with events `run:selected` (run id), `run:updated` (C3 run detail),
  `node:selected` (node id), `graph:loaded` (C3 graph), `ui:error` (a short reason;
  `app.js` shows the error badge), and `state` holds
  `{graph, runId, run}`. A module touches only DOM under its own `el` plus
  `document.body`-level overlays it creates. Every interactive element has a
  `data-testid`; tests select by `data-testid` or role, never by CSS class or text.

### Conventions
- Visual first, minimal text: icons, colour, motion; words only in tooltips,
  `aria-label`s and tiny secondary labels. Every animated state also has a static
  visual cue so `prefers-reduced-motion` users and screenshots can read it.
- Accessibility: buttons are `<button>`, icons have `aria-label`, the modal traps
  focus, colour is never the only state signal (shape/icon too).
- Python: small pure functions, typed, standard library first. FastAPI only in
  `pipeline/ui/`. No new runtime dependency beyond `fastapi` and `uvicorn`.
- JS: plain ES2022 modules, no TypeScript, no build step. Dev dependencies only in
  `pipeline/ui/e2e/package.json`, pinned exactly, with a committed lockfile.
- Browser coverage: the gate is 100% lines, functions and branches for every file
  in `pipeline/ui/static/js/`. Do not add ignore comments to reach it; delete
  unreachable code instead. The suite must stay deterministic: wait on
  `expect(...).toHave*` and `data-testid`/`data-state` attributes, never on
  `waitForTimeout`.
- Each PR that changes a rendered surface carries screenshots: commit PNGs under
  `docs/screenshots/pipeline-console/` and link them by raw URL at the commit SHA
  (`https://raw.githubusercontent.com/gonandrap/painforwisdom/<sha>/docs/screenshots/...`);
  one per state the change claims. Passing tests are not evidence of layout.
- In the PR body include the exact test commands you ran and their tail output.

### Traps
- LangGraph runs parallel branches in a thread pool: anything appending to a file
  from node context needs a lock and whole-line writes.
- kb_curator's `interrupt()` re-runs the node from its top on resume, and the
  summarizer fires three times (two early `{}` fires); branch validators can
  re-fire. The UI must not flicker a `done` node back to `running` on a `noop`.
- runs.jsonl stage names are not graph node names (`extract-image` vs
  `extract_image`; legacy runs log `validator` for the summarizer); always go
  through `pipeline/ui/mapping.py`.
- Each feature module has its own CSS file (`css/launch.css`, `css/drawer.css`,
  `css/telemetry.css`); never edit another module's JS or CSS, or parallel PRs
  conflict.
- `processed/` is resolved from the code checkout (`PROJECT_ROOT`), so a worktree
  writes runs into its own `processed/`. `.env.sandbox` exists only in the dev
  checkout.
- The UI server must never call `load_dotenv` and never import `pipeline.run`.
- Never run the real pipeline with `--profile prod` from a test, and never in CI.
  Real runs (section 10 only) use `--profile sandbox` after `tests/sandbox_reset.sh`.
- Do not change node logic, prompts, models, Notion schemas, or the vault to make
  the UI easier. If the console needs data the pipeline does not record, raise it
  as a question instead of changing a node (section 4's `page_ids` is the only
  sanctioned exception).

### Never
- Never edit the main checkout; work in your worktree. Never commit to `main`,
  force-push, or merge your own PR.
- Never widen your section: if a sibling section's module is wrong, make the
  smallest fix with a test and say so in the PR, or file
  `jarvis backlog add painforwisdom "..."`.
- Never weaken a gate (coverage threshold, CI job, test skip) to get green.
