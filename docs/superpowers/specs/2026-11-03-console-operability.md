# Pipeline console: service, live debug, operability and publishing (2026-11-03)

Feature order fo-ef4b4917. Follow-up to `docs/superpowers/specs/2026-10-31-pipeline-console-ui.md`
(the "console spec"), after the first real prod run through the console
(`2026-11-03_055748_ui`). It covers the user's items 1-9 and 11 (there is no item 10).

Sections 2-8 are one work order each. Section 1 is shared reference, owned by no
work order. Every contract the console spec defines (C1-C7) still holds unless
section 1.2 amends it here.

## 1. Architecture and contracts (reference; no work order)

### 1.1 What exists today

- The console (`pipeline/ui/`, FastAPI + plain ES modules) is started by hand:
  `python -m pipeline.ui --profile prod` from the tag-pinned live checkout
  `~/workspace/painforwisdom-live`. Its state root is the dev folder
  `~/workspace/painforwisdom/painforwisdom`, set with `PAINFORWISDOM_STATE_ROOT`,
  and `.env` holds the prod secrets. Releases go through
  `services/coach/scripts/ship-it.sh`, which runs `deploy-live.sh <tag>`. Both
  scripts use the shared `v*` tags.
- Tokens are recorded only in `runs.jsonl` (C4), one row per stage written when the
  node ENDS. `pipeline/llm.py:call_llm` makes one `litellm.completion` call and
  does not stream.
- YouTube (`pipeline/nodes/youtube_upload.py`, `pipeline/youtube_client.py`) and
  WordPress (`pipeline/nodes/wordpress_draft.py`, `pipeline/wordpress_client.py`)
  exist but are dormant behind the env vars `YOUTUBE_ENABLED` and
  `WORDPRESS_ENABLED`. Both write a bundle to disk. In the real run the YouTube
  metadata agent returned an empty title. WordPress.com's free plan blocks REST
  writes, so a real WordPress publish needs the Personal plan. That is the user's
  purchase; the code does not depend on it.

### 1.2 Contracts added or amended by this feature

Each contract has one owner section. Other sections read it and never change it.

- **C1 amendment, `end.result`** (owner: section 3). Every C1 `end` event gains
  `"result"`, a dict built from the node's returned state update. It holds:
  - every scalar value (str truncated to 300 chars, int, float, bool, None);
  - every key ending in `_findings` or `_verdict`, with lists capped at 50 items
    and each item JSON-serialised to at most 500 chars.

  Keys ending in `_path` stay in `outputs` as before. `result` is additive: readers
  must accept events without it (old runs).
- **C3 amendment, run detail** (owners: section 3 for `nodes`, section 4 for
  `telemetry`):
  - `nodes.<n>` gains:
    - `"result"`: the last non-noop `end.result`, or `{}`;
    - `"facts"`: the node's last C4 row, without `ts`, `stage`, `per_call`,
      `cost_usd`, `total_cost_usd` and the four `runs.TOKEN_FIELDS`;
    - `"skip_reason"`: a str or null.

    `state` is `"skipped"` when the node ended and its last C4 row has
    `"skipped": true`.
  - `telemetry.per_node.<n>` gains `calls` (int), `in_flight` (count of C8
    calls started and not ended) and `live` (bool: the numbers came from C8, not
    C4).
  - `telemetry` gains `calls` and `in_flight` totals.
- **C8 `usage.jsonl`** (owner: section 4). One JSON object per line in
  `<run_dir>/usage.jsonl`. There is one line per LLM call boundary:
  `{"ts": <epoch float>, "event": "start"|"end"|"error", "call_id": "<hex>",
  "node": "<graph node>", "attempt": <int>, "model": "<id>"}`. `end` adds
  `"input_tokens", "output_tokens", "cache_read_tokens",
  "cache_creation_tokens", "cost_usd", "call_s"`. `error` adds `"error"`.
  Lines are whole-line writes under a lock, and nothing is written outside a node
  (scripts, tests).
- **C9 publish package** (owner: section 6). One file,
  `<run_dir>/<node dir>/package.json`. The node dir is `wordpress-draft/` for
  `wordpress` and `youtube-upload/` for `youtube`. It is replaced atomically
  (write temp, `os.replace`):
  ```json
  {"schema": 1, "target": "wordpress"|"youtube",
   "mode": "off"|"manual"|"auto",
   "status": "pending"|"publishing"|"published"|"failed",
   "fields": {"<name>": <value>},
   "field_specs": {"<name>": {"type": "string"|"text"|"markdown"|"tags"|"enum"|"url",
                              "label": "...", "max": <int|null>, "options": [..]|null,
                              "required": <bool>}},
   "readonly": {"<name>": <value>},
   "assets": [{"role": "featured_image"|"video", "path": "<abs>"}],
   "result": {"id": "...", "url": "..."}|null,
   "error": "<str>"|null, "updated_at": <epoch>}
  ```
  The package describes its own fields, so the operability view (section 8)
  renders an editor for any target without knowing which one it is. A
  `publish.lock` file next to it, created with `O_EXCL`, marks a publish in
  progress.
- **C10 `config/publishing.json`** (owner: section 6). Committed, pipeline-owned and
  non-secret. Per Neo's ruling on Q3268 it is the "catalog" the user named. It
  replaces `config/youtube_metadata.json`, whose fields move under `youtube`:
  ```json
  {"schema": 1,
   "youtube": {"mode": "manual", "kind": "short", "privacy_status": "private",
               "category_id": "22", "made_for_kids": false, "default_tags": [...],
               "description_suffix": "", "scraped_at": null, "scraped_from_channel": "@painforwisdom"},
   "wordpress": {"mode": "manual", "site": "painforwisdom.wordpress.com", "status": "draft",
                 "auth_mode": "app_password", "categories": [], "default_tags": []},
   "profiles": {"sandbox": {"youtube": {"mode": "off"}, "wordpress": {"mode": "off"}}}}
  ```
  `pipeline/publishing_config.py:load(profile) -> dict` deep-merges
  `profiles[profile]` over the base. `mode` replaces `YOUTUBE_ENABLED` and
  `WORDPRESS_ENABLED`; both env vars are deleted from the code. Secrets stay in
  the profile env file (`.env` / `.env.sandbox` under STATE_ROOT), never in this
  file:
  - `YOUTUBE_CLIENT_ID`, `YOUTUBE_CLIENT_SECRET`, `YOUTUBE_REFRESH_TOKEN`;
  - `WORDPRESS_USERNAME`, `WORDPRESS_APP_PASSWORD`, `WORDPRESS_OAUTH_TOKEN`.

  `scripts/scrape_youtube_tags.py` writes `youtube.default_tags` in this file.
- **C3 additions, publish API** (owner: section 6):
  - `GET /api/runs/{id}/publish` returns `{"targets": {"<target>": <C9 package>}}`,
    or `{}` when the run has none.
  - `POST /api/runs/{id}/publish/{target}` takes the body `{"fields": {...}}`. It
    returns 202 with the package in status `publishing`. Errors:
    - 400 for a field not in `field_specs`, a wrong type, over `max`, or a
      missing `required` field;
    - 404 for an unknown run or a run without that package;
    - 409 `{"detail": "run active"}` while the run is active;
    - 409 `{"detail": "already published", "url"}`;
    - 409 `{"detail": "publishing"}` while the lock exists.

### 1.3 Publishing flow (sections 6 and 7)

The node builds the package. The mode decides what happens next:

| mode | node writes | C4 row | DAG state |
|---|---|---|---|
| `off` | no package | `skipped: true, reason: "off"` | skipped (yellow) |
| `manual` | package `pending` | `skipped: true, reason: "manual"` | skipped (yellow) |
| `auto` | package, then publishes in-process | the result, or `error` with the package `failed` | done |

A manual confirm reaches `POST .../publish/{target}`. The server checks the
fields, writes them into the package, sets `publishing`, and spawns
`python -m pipeline.publish --profile <run profile> --run-dir <rd> --target <t>`
detached, reaped the way `pipeline/ui/launcher.py:spawn` reaps runs.

The UI server must never load secrets (console spec Trap: no `load_dotenv`). The
CLI loads the profile env file itself, then calls the same adapter the node
calls in `auto` mode, and writes `published` with `result` or `failed` with
`error`. It removes the lock in a `finally`.

Adapters live in `pipeline/publish/` (`__init__.py` holds the registry
`TARGETS: dict[str, Callable[[Path, dict], dict]]`), so the node and the CLI
share one code path.

### 1.4 Order and file ownership

- Sections 2, 3 and 4 start in parallel.
- Section 6 needs 3: it flips a fixture row that 3 writes, and both edit the
  fake runner.
- Section 5 needs 3 and 4.
- Section 7 needs 3 and 6.
- Section 8 needs 3 and 6.

The fake runner (`pipeline/ui/fake_runner.py`) and the fixture run
`tests/fixtures/ui/processed/fx-done` are the only e2e data sources. Each
section extends them for its own contract and rebases on the sections merged
before it.

`pipeline/graph.py:_instrumented` is touched by 3 (`result`) and 4 (contextvar
binding). `build_graph` is touched by 7 only.

This feature lifts the console spec Trap "do not change node logic" for exactly
these changes:
- section 3's `_instrumented` hunk;
- section 4's `call_llm` hook;
- section 6's WordPress node;
- section 7's YouTube nodes and prompt.

## 2. Console as a user service, installed by every ship

Item 1: the console must run as a service, and shipping a release must install
the new version, the way Jarvis OS does it (`scripts/shipit.sh` re-renders its
units through `install_prod_service.sh --no-restart` on every release, then
restarts).

2.1 Add the unit `pipeline/systemd/painforwisdom-console.service`:
- `WorkingDirectory` is the live checkout;
- `ExecStart` is the conda env python
  (`~/miniconda3/envs/painforwisdom-poc/bin/python -m pipeline.ui --profile prod`);
- `Environment=PAINFORWISDOM_STATE_ROOT=<dev folder>`;
- an explicit `PATH` that holds the conda env bin, ffmpeg, `claude` and the
  base dirs. Under systemd the server's env is every spawned run's env, so a
  missing binary is a run failure;
- `Restart=always`, `RestartSec=5`;
- **`KillMode=process`**. The launcher spawns runs detached inside the service's
  cgroup, and the default `control-group` would kill an in-flight run on every
  restart;
- `WantedBy=default.target`.

The installer writes the paths in from its own variables (`LIVE_DIR`,
`STATE_ROOT`, conda env), never as hard-coded copies.

2.2 Extend `pipeline/scripts/install-pipeline-units.sh`:
- install the console unit and `enable --now` it. The timers stay not-enabled,
  as today;
- add the flags `--dry-run` (print the plan and the rendered unit, touch
  nothing), `--unit-dir <dir>` and `--no-restart` (the caller restarts).

2.3 Extend `services/coach/scripts/deploy-live.sh`:
- **Before** the checkout, refuse with exit 1 and the message
  `pipeline run <run_id> active; retry after it ends` when any
  `<processed>/*/langgraph/run.json` is in an active status with a live pid.
  `DEPLOY_IGNORE_ACTIVE_RUN=1` overrides. Agent prompts are read from disk at
  node time, so checking out a new tag mid-run would give a mixed-version run.
- After the coach health check, run
  `install-pipeline-units.sh --no-restart`, then
  `systemctl --user restart painforwisdom-console.service`.
- Wait up to 30 s for `GET http://127.0.0.1:8820/api/graph` to return 200. A
  timeout fails the deploy.

Paths are overridable so tests never touch real dirs:
- the installer: `PFW_LIVE_DIR`, `PFW_STATE_ROOT`, `PFW_PYTHON`;
- deploy-live: `PFW_PROCESSED_ROOT`, which defaults to `$LIVE_DIR/processed`
  because the prod console writes runs into the live checkout's `processed/`.

The installer's pre-checks (the runtime.py grep, `.env` present) run against
the overridden paths, so `--dry-run` works in a test tmp tree. Tests stub `git`,
`docker`, `curl` and `systemctl` on PATH, in the style of
`services/coach/tests/test_ship_it.py`.

Update `.claude/skills/coach-ship-it/SKILL.md` and `OPERATIONS.md` §9 to say a
ship now also deploys the console, and how to stop the hand-started one before
the first install.

2.4 Out of scope: launching runs on a timer or inbox watch. The pipeline is still
launched from the console.

## 3. Every node shows its data; wide drawer; real markdown; skipped is yellow

Items 2, 5, 6 and 7.

3.1 **`end.result`** (C1 amendment, section 1.2). Built in
`pipeline/graph.py:_instrumented` by a small pure helper with its own test.

3.2 **Run detail** (C3 amendment, `nodes` part) in `pipeline/ui/runs.py`:
`result`, `facts` and `skip_reason`, plus the `skipped` derivation from C4 rows
through `mapping.node_for_stage`. A `skipped` node is never `pending` and never
flickers back to `running` on a noop re-fire. Legacy runs keep their current
behaviour, plus `skipped` from rows.

3.3 **Artifact attribution** in `pipeline/ui/artifacts.py`: build a map from path
to node from every `end.outputs`, including paths inside the run dir, and use it
before `DIR_TO_NODE`. Then the featured image (written by `extract_image` into `wordpress-draft/`;
`featured.jpg` in real runs, `featured.png` in `fx-done`) belongs to
`extract_image`. `fx-done`'s `extract_image` end event gains
`outputs.featured_image_path`, and `inspect.spec.js` opens the image from
`extract_image`.

3.4 **Drawer body for every node** (`pipeline/ui/static/js/drawer.js`): a facts
block (C4 `facts` plus `result` scalars as a key/value list) and a findings list
(each `*_findings` item with its severity or verdict as an icon) above the
artifacts. A node with nothing at all shows the existing "unavailable" glyph,
never an empty panel.

3.5 **Width** (`css/drawer.css`):
- default width `clamp(480px, 38vw, 1600px)`;
- a drag handle on the left edge (`data-testid drawer-resize`), keyboard
  resizable with ←/→ when focused;
- the width persisted in `localStorage` inside try/catch;
- text wraps (`white-space: pre-wrap; overflow-wrap: anywhere`), so the drawer
  never scrolls horizontally. Only a wide table or code block scrolls, inside its
  own box;
- the result modal gets `min(1200px, 92vw)`.

3.6 **Markdown** (`pipeline/ui/static/js/markdown.js`, new, exports
`renderMarkdown(src: string) -> string`):
- escape first, then add tags;
- supports ATX headings, paragraphs, bold/italic, inline code, fenced code,
  ordered and unordered lists (one nesting level), blockquotes, hr, GFM pipe
  tables, and http(s) links (`rel="noopener"`, `target="_blank"`);
- replaces `drawer.js`'s `inline`/`markdown`.

No vendored library: everything in `static/js/` is under the 100% coverage gate,
and moving a library out of it would weaken the gate. Section 8 reuses this
module.

3.7 **Skipped colour**: `app.css` gains a `--skipped` amber token (light and
dark). `.node[data-state="skipped"]` uses it for the disc stroke and the cue.
The dash cue stays, so colour is never the only signal. The drawer state cue is
amber too.

3.8 **Fixtures**: the fake runner emits `skipped: true, reason: "off"` C4 rows for
`youtube_upload` and `wordpress_draft`, and `result` on `end` events. `fx-done`
gains the same rows (rewriting its existing `youtube-upload` row
`reason: "dormant"` to `"off"`). Section 6 later flips the WordPress row to
`"manual"` when it adds the package. `fx-done` also gains plus validator `end.result` findings. Update every spec
that assumed `fx-done` is all `done`.

## 4. Per-call LLM usage recorded live

The data for item 4.

4.1 `pipeline/usage.py` (new):
- a `contextvars.ContextVar` holding `(run_dir, node, attempt)`;
- `bind(run_dir, node, attempt)`, a context manager;
- `record_start(model) -> call_id`;
- `record_end(call_id, usage, cost_usd, call_s)`;
- `record_error(call_id, exc)`.

Writes are whole-line, under a module lock, to `<run_dir>/usage.jsonl` (C8). When
nothing is bound, all three are no-ops.

4.2 `pipeline/graph.py:_instrumented` binds around `fn(state)`, on the node's own
thread, and resets in `finally`. `pipeline/llm.py:call_llm`:
- calls `record_start` before each attempt;
- calls `record_end` with `_extract_usage` output on success;
- calls `record_error` on a failure.

Search for any other LLM entry point (`litellm.completion`, `claude -p`
subprocesses) under `pipeline/nodes/` and hook it the same way, or list it in
the PR as not covered.

4.3 `pipeline/ui/runs.py:telemetry` (C3 amendment, `telemetry` part). Per node:
- once the node has a C4 row with token fields, C4 is final and C8 is ignored
  for it;
- before that, sum C8 `end` lines and set `live: true`;
- `calls` counts C8 `end` lines (C4 `llm_calls` when only C4 exists);
- `in_flight` counts `start` lines without a matching `end`/`error` for the same
  `call_id`, only while the run status is active (C2 status with a live pid). A
  finished or crashed run always reports `in_flight` 0.

Totals sum the per-node values. No double counting: test a node that has both a
C4 row and C8 lines.

4.4 The fake runner writes C8 `start`/`end` pairs for its `_LLM` nodes, spread
over the node's step time so a live run shows growing numbers. Add the matching
lines to `fx-running`, which then has one call in flight on `extract`. That shows
only when a test makes the run live (pid rewrite); with pid 0 the run reads as
crashed and `in_flight` is 0.

4.5 Out of scope: token streaming inside one call (file a backlog item). A long
call shows as in flight with its elapsed time, and its tokens land when it ends.

## 5. Live debug panel and a readable Gantt

Items 3 and 4, the UI part. Data: C3 `telemetry`, as amended by section 4.

5.1 **Gantt** (`js/telemetry.js`, `css/telemetry.css`):
- row icons at least 22 px;
- each bar shows its total tokens (`input + output + cache_read +
  cache_creation`, compact like `12.3k`) as a label right-aligned in a fixed
  right-hand column, always visible, not only on hover;
- nodes without tokens show `–`;
- the hover tooltip keeps name, model and the token breakdown;
- skipped bars use the `--skipped` amber from section 3.7;
- the label column lines up across rows so heavy consumers stand out.

5.2 **Debug panel** (new `js/debug.js`, `css/debug.css`, one slot
`<aside id="debug">` in `index.html`, one `mount` in `app.js`): a compact overlay
in the DAG area's top-right corner (`data-testid debug`), collapsible.
- **Fields:**
  - tokens in, out, cache read and cache write;
  - total tokens;
  - cache hit ratio (`cache_read / (input + cache_read)`);
  - LLM calls and calls in flight;
  - cost_usd;
  - elapsed and work time;
  - model(s).
- **Scope:** it shows the run by default. On `node:selected` it shows that node
  only (`data-scope="<node>"`); clicking the selected node again, or the panel's
  scope chip (`data-testid debug-scope-clear`), returns it to the run. A node
  that has ended shows its final numbers. A running node is marked live
  (`data-live="true"`).
- **Refresh:** it re-renders on every `run:updated`, i.e. the existing run poll,
  at least once every 5 s. It adds no polling of its own.

5.3 Screenshots of a live fake run, run-scope and node-scope, plus a finished
run, under `docs/screenshots/pipeline-console/`.

## 6. Publishing core and WordPress

Item 11, plus the shared core section 7 plugs into. Contracts C9, C10 and the
C3 publish API, all from section 1.2.

6.1 **Config.**
- Write `config/publishing.json` and `pipeline/publishing_config.py` (C10).
  Merge `config/youtube_metadata.json` into it, then delete the old file.
- Point `pipeline/nodes/youtube_upload.py` and `scripts/scrape_youtube_tags.py`
  at the new loader, keeping today's behaviour. Section 7 reworks YouTube.
- The profile is the one `pipeline/run.py` was started with (`_PROFILE`). Expose
  it to nodes through state or the env var `PIPELINE_PROFILE`, set once by
  `run.py`.
- Remove the env-var flags (`WORDPRESS_ENABLED` here; section 7 removes
  `YOUTUBE_ENABLED`) and their docs in `OPERATIONS.md`/`README.md`.

6.2 **Package core** (`pipeline/publish/`):
- `package.py`: read, atomic write, the lock (`O_EXCL`), and
  `validate_fields(package, fields) -> list[str]` against `field_specs`;
- `__init__.py`: the `TARGETS` registry;
- `__main__.py`: the CLI from section 1.3, which loads the profile env file like
  `run.py` does;
- `wordpress.py`: the adapter.

6.3 **WordPress node** (`pipeline/nodes/wordpress_draft.py`). Keep building the
bundle (post.md, post.html, meta.json, featured.jpg), and also write the C9
package:
- editable fields: `title` (string, max 200, required), `body` (markdown,
  required), `excerpt` (text, max 300), `tags` (tags), `categories` (tags),
  `status` (enum from `draft`/`publish`, default from config), `youtube_url`
  (url, may be empty);
- `assets`: the featured image.

Then follow the mode table in section 1.3. The adapter does what today's live
path does: media upload, then the post, then the Notion write-back of the
WordPress URL. It renders `body` to HTML with the code that makes `post.html`
today. Update `pipeline/nodes/validators/branch_wordpress.py` so `off` and
`manual` are not failures.

6.4 **Server** (`pipeline/ui/publish.py`, `pipeline/ui/routes_publish.py`, mounted in
`app.py`): the C3 publish API. Keep it out of `runs.py`.
- Spawning reuses the launcher's detached spawn and reap. Do not reuse the name
  `launcher.publish_upload`, which means inbox upload.
- With `PIPELINE_UI_RUNNER=fake`, spawn a fake publisher instead
  (`python -m pipeline.ui.fake_runner publish ...`). It sleeps `step_ms`, then
  writes `published` with `result.url = "https://example.invalid/<target>/<run>"`,
  or `failed` when the field `title` equals `FAIL` (for the error path).
- The UI server must not import the WordPress/YouTube clients or anything that
  reads secrets.

6.5 **Fixtures**: `fx-done` gains a `wordpress-draft/package.json` in status
`pending`. A new run `fx-published` has the WordPress package `published` (with a
url) and a YouTube package `pending` shaped as section 7 defines (`title`,
`description`, `tags`, `privacy_status`, `kind`). Section 8 tests against both. The WordPress C4 row in `fx-done` flips from
`reason: "off"` (set by section 3) to `"manual"`.
The fake runner writes a WordPress package in `manual` mode.

6.6 No real WordPress call in tests: mock `httpx` at the client boundary.
Real-publish evidence goes in the PR's "How to validate in production" section,
because the free plan blocks writes.

## 7. YouTube: metadata node, package, upload

Item 8. It plugs into the section 6 core.

7.1 **Root cause first.** In run `2026-11-03_055748_ui` the metadata agent's title
was empty. Check `stop_reason` and the raw text: `max_tokens=600` in
`pipeline/nodes/youtube_upload.py` and a fence or prose wrapper the parser
rejects are the two suspects. Write a failing test from that response shape
before fixing.

7.2 **New node `youtube_metadata`** (item 8.1). Topology changes from
`extract → youtube_upload → bv_youtube` to
`writer → youtube_metadata → youtube_upload → bv_youtube`, so the metadata sees
the blog title and post.
- It calls the `youtube-upload-agent` prompt (`pipeline/agents/youtube-upload-agent.md`,
  which you may edit) for `title` (≤100 chars), `description` (a short phrase plus
  the config `description_suffix`) and `tags_extra`. It merges `default_tags`
  from C10, and writes `youtube-upload/metadata.json` (output key
  `youtube_metadata_path`). `youtube_upload` stops returning
  `youtube_metadata_path`, so the file is attributed to `youtube_metadata`
  alone.
- It logs a C4 row, stage `youtube-metadata`, with tokens.
- An empty title after one retry is not an exception. It writes the metadata
  with an empty title and a `warning`, so the package needs the user to fill it.

7.3 **`youtube_upload`**:
- builds the C9 package from the metadata and C10:
  - fields: `title` (string, max 100, required), `description` (text, max
    5000), `tags` (tags; the YouTube 500-char total is checked in
    `validate_fields` via `max`), `privacy_status` (enum from
    `private`/`unlisted`/`public`), `kind` (enum from `short`/`video`);
  - `assets`: the source video;
  - `readonly`: the duration in seconds, read with ffprobe;
- then follows the mode table in section 1.3.

`pipeline/publish/youtube.py` wraps `pipeline/youtube_client.upload_draft_short`
and handles `kind`:
- `short` appends `#Shorts` to the description when missing;
- a `short` longer than 180 s goes to `failed` with a clear error;
- `video` is uploaded as is.

Register `youtube` in `TARGETS`. Update
`pipeline/nodes/validators/branch_youtube.py` for the modes. Delete
`YOUTUBE_ENABLED`.

7.4 **Topology fallout**, all in this section:
- `pipeline/graph.py:build_graph` and its docstring diagram;
- `pipeline/ui/mapping.py`: all three maps plus `NODE_ICON` (`tag`, added to
  `static/js/icons.js`); `youtube-upload/` stays attributed to `youtube_upload`;
  the metadata file reaches `youtube_metadata` through section 3's
  `end.outputs` attribution;
- the fake runner's topology, `_LLM` and `ARTIFACTS`;
- `fx-done` events and rows, including the new node, or it renders pending in a
  done run;
- the README and OPERATIONS pipeline diagrams;
- `kn-25ffb5c1`, which describes the old topology: add a superseding learning.

7.5 No real YouTube call in tests: mock the client boundary. The real upload is
validated by the user from the operability view after release, and the PR says
how.

## 8. Operability view

Item 9 plus the edit-and-confirm halves of items 8.2 and 11.

8.1 **Module.** New `js/operability.js` and `css/operability.css`, one slot in
`index.html`, one `mount` in `app.js`, and new `api.js` functions
(`publishPackages(runId)`, `publish(runId, target, fields)`).
- It is a full-height panel that replaces the DAG area while open. It opens from
  a header button (`data-testid output-open`, enabled once a run is selected).
  `Esc` or `data-testid output-close` closes it, and the route
  `#/runs/<id>/output` is deep-linkable.
- It replaces the trophy result modal. Delete the modal from `drawer.js`, since
  section 3 has merged, and keep `GET /result` as a data source.

8.2 **Cards**, each with `data-testid output-<name>`, each hidden when its data
is absent, in this order:
1. thumbnail: the featured image artifact at full card width;
2. blog post: `blog_post.md` rendered with section 3's `markdown.js`, with the
   Notion page link from `/result`;
3. YouTube package;
4. WordPress package;
5. knowledge base: `vault_entry_path` and `curator_summary_path` from
   `kb_curator`'s `end.outputs` (the summary lists the themes touched), each
   rendered inline through the artifacts API, with an
   `obsidian://open?path=<abs>` link on the vault entry;
6. research: the CSV as a table, plus the Notion research page links from
   `/result`;
7. audit: `audit_report.md` rendered.

Fixtures for this section: point `fx-done`'s `vault_entry_path` at a committed
file under `tests/fixtures/ui/vault/`, instead of `/nonexistent`, so the card
renders.

8.3 **Package editor**, generic over C9 `field_specs`:
- inputs by type: string becomes `input`, text and markdown become `textarea`
  (markdown with a live preview), tags become chip input, enum becomes
  `select`, url becomes `input type=url`;
- client-side max/required checks mirror the server's, and a 400 from the server
  is shown per field;
- `readonly` values and `assets` are shown (image or video thumbnail
  placeholder);
- the confirm button is `data-testid publish-<target>`. It POSTs, then polls
  `GET /publish` every 2 s while `publishing`, and shows the `result.url` link
  (`data-testid published-<target>`) or the `error`;
- `published` packages render read-only with their link;
- a pre-fill rule: when the WordPress package's `youtube_url` is empty and the
  YouTube package is `published`, pre-fill it with that url, and let the user
  edit it.

8.4 Tests: Playwright against `fx-done`, `fx-published` and a fake run, covering:
- an edit, then a confirm, then published;
- the `FAIL` error path;
- a 409 while publishing (plant `publish.lock` in the test's copy of the run;
  `page.clock` cannot pace the server-side fake publisher);
- the deep link;
- `e2e/tests/real.spec.js` updated from `result-open` to `output-open` (CI does
  not run it, so nothing else will catch the break);
- every card hidden when its data is absent.

Screenshots of each card state.

## Agent profile

You are a worker on the painforwisdom pipeline console feature
`docs/superpowers/specs/2026-11-03-console-operability.md`. You receive one numbered section
of that spec and a short margin note. Build exactly that section, finish it with
one pull request, and stop.

### Codebase you are in
- `pipeline/` is a LangGraph content DAG (`pipeline/graph.py`), plain Python, conda
  env `painforwisdom-poc`. Run it as `python -m pipeline.run`. Nodes are in
  `pipeline/nodes/`, and stage prompts are in `pipeline/agents/*.md` (read by
  `pipeline/runtime.py:load_agent_prompt`).
- `pipeline/ui/` is the console. FastAPI with plain ES modules under
  `pipeline/ui/static/js/`, one CSS file per module under `static/css/`. It
  reads runs from `processed/<run_id>/langgraph/` (`events.jsonl` C1, `run.json`
  C2, `runs.jsonl` C4). Its design and contracts C1-C7 are in
  `docs/superpowers/specs/2026-10-31-pipeline-console-ui.md`. Read that spec's
  `Contracts`, `Conventions` and `Traps` with
  `jarvis spec section <path> <name>`, never the whole file. This spec's section
  1.2 adds C8-C10 and amends C1/C3.
- Prod runs from the tag-pinned checkout `~/workspace/painforwisdom-live`. State
  (`.env`, `inbox/`, the vault) lives in the dev folder via
  `PAINFORWISDOM_STATE_ROOT` (`pipeline.runtime.STATE_ROOT`). Releases:
  `services/coach/scripts/ship-it.sh`, then `deploy-live.sh`, shared `v*` tags.
- `obsidian-vault/` is a git submodule. Never commit inside it.
- Serena is wired to this repo. Use `find_symbol`, `find_referencing_symbols` and
  `get_symbols_overview` for code, and grep only for text.

### Tests (`.github/workflows/ci.yml` is the reference)
- Pipeline: from the repo root,
  `/home/gonzalo/miniconda3/envs/painforwisdom-poc/bin/python -m pytest tests -q -rs`.
- Browser:
  `cd pipeline/ui/e2e && PYTHON=/home/gonzalo/miniconda3/envs/painforwisdom-poc/bin/python npm test`.
  - The coverage gate is 100% lines, functions and branches for every file in
    `static/js/`, and it is checked in global teardown. So a partial run (grep,
    one file) always fails the gate. Run the whole suite before claiming green.
  - Keep JS branchless where a race decides a branch.
  - Drive polling with `page.clock.install()` + `clock.runFor()`; never use
    `waitForTimeout`.
  - Allow intended console noise with `errors.allow(/regex/)`. Expected
    400/409 responses print `Failed to load resource` errors.
  - Never run `npm test` concurrently with `npm run test:real`.
- Screenshots: `PFW_SHOTS=<dir> npm test`. Commit PNGs under
  `docs/screenshots/pipeline-console/` and link them in the PR by raw URL at the
  commit SHA.
- `python` on PATH in a Jarvis worker is the Jarvis venv, not the conda env.
  Always use the absolute conda python.

### Conventions
- Smallest change that fully solves your section. Finish what it breaks: callers,
  tests, fixtures (`tests/fixtures/ui/processed/`), the fake runner
  (`pipeline/ui/fake_runner.py`), docs (`OPERATIONS.md` §9, `README.md`).
- Python: small typed pure functions, standard library first. No new runtime
  dependency beyond what `pipeline/requirements.txt` already has.
- JS: plain ES2022 modules, no framework, no bundler, no CDN, no vendored library.
  Every interactive element has a `data-testid`. Tests select by `data-testid` or
  role.
- Visual first: icons, colour and motion, with words in tooltips and
  `aria-label`. Colour is never the only state signal.
- Each contract has one owner section (spec 1.2). Read the others; change only
  your own.
- New non-trivial logic gets one focused test. The PR body carries the exact
  test commands you ran and their tail output, plus screenshots for every
  rendered state you changed.
- Use the `open-a-pull-request` skill to open the PR.

### Traps
- LangGraph runs parallel branches in a thread pool. Any file append from node
  context needs a lock and whole-line writes. Contextvars must be set inside the
  node wrapper, on the node's own thread.
- kb_curator's `interrupt()` re-runs the node from the top, and the summarizer
  fires three times (two noop fires). Never flicker a done or skipped node back
  to running on a noop.
- runs.jsonl stage names are not graph node names. Always map through
  `pipeline/ui/mapping.py`.
- The UI server never calls `load_dotenv`, never imports `pipeline.run`, and
  never imports a client that reads secrets. Anything needing secrets runs in a
  spawned subprocess that loads the profile env itself.
- `pipeline/ui/app` imports `pipeline.graph`, which imports every node. Do not
  add an import-time side effect to a node.
- A pytest that builds the graph must monkeypatch `pipeline.graph.CHECKPOINT_DB`.
- `fx-running`, `fx-hitl` and `fx-crashed` use pid `0`, which reads as dead.
  Only tests that want a live run rewrite it.
- `processed/` resolves from the code checkout. A worktree writes runs into its
  own `processed/`. `.env.sandbox` exists only in the dev checkout.
- A systemd restart with the default `KillMode` kills runs the console spawned.

### Never
- Never run the real pipeline with `--profile prod`, never publish to the real
  YouTube or WordPress, and never call Notion from tests or CI. Mock at the
  client boundary.
- Never install, enable, start or restart a systemd unit on this host, and never
  touch `~/workspace/painforwisdom-live`. Prove scripts with `--dry-run` and
  `--unit-dir <tmp>`. A service restart is a gated, privileged action, and
  production evidence belongs in the PR's "How to validate in production"
  section.
- Never commit secrets. Credentials go only in the gitignored profile env files.
- Never edit the main checkout, commit to `main`, force-push, or merge your own
  PR.
- Never widen your section. If a sibling's module is wrong, make the smallest fix
  with a test and say so in the PR, or `jarvis backlog add painforwisdom "..."`.
- Never weaken a gate (coverage threshold, CI job, test skip) to get green.
