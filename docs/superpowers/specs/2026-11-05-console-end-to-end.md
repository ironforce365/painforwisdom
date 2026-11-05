# Pipeline console: operate the pipeline end to end from the UI

Guiding rule: **every branch of a pipeline run has a user action in the console.** Telegram stays a notification and a second answer channel, never the only one.

Builds on `2026-10-31-pipeline-console-ui.md` and `2026-11-03-console-operability.md`. Where this spec differs, this spec wins.

## 1. Why fo-ef4b4917 did not deliver what was asked

| Symptom | Root cause |
|---|---|
| Run shows no failure reason | `run.py` keeps the prompt in a Telegram call and writes only `status=waiting_reply`. The drawer never renders `node.error`. No banner exists. |
| No way to answer | `_ask_bounded` / `_ask_indefinitely` block on `telegram_io.sh ask`. No console reply path, no stop, no retry (`pipeline.retry` is CLI only and does not update `run.json`). |
| Editors missing in 2026-11-03 run | Packages are written only by runs newer than the feature. Nothing can create one for an older run, so the cards hide. |
| Edits only saved by publishing | `POST publish` is the only write path. No save, and blog, KB entries have no editor at all. |
| Research not clickable | Local books have `file://` URLs. `linkEl` renders a non-http URL as a `<span>`. |
| $0 cost | `llm.py` hardcodes 0 under subscription billing. `telemetry.js` hides the cost then. |
| Graph is an image | Static `viewBox`, no pan or zoom. The drawer is `position: fixed` over it. |
| Bottom panel | A 16-row Gantt strip, numbers hidden on hover, in a fixed-height slot. |
| `ALREADY_PROCESSED` | The LLM returned an action the node does not know. The prompt does not define it. A retry repeats it, and the run cannot proceed. |

## 2. Decision tree

Each leaf has an id. `pipeline/ui/e2e/decision_tree.json` is the machine-readable copy. `tests/test_decision_tree.py` fails unless every id is tagged `[DT-x]` in at least one unit test, one integration test and one browser spec (a leaf's `levels.unit` or `levels.integration` in the JSON may hold the reason for an exemption instead of `true`; browser is never exempt).

```
START
├─ S1 inbox empty ─────────────────────────▶ upload a file
├─ S2 inbox has inputs ────────────────────▶ choose; toggle auto-approve
├─ S3 input already ran ───────────────────▶ tile warns and names the runs; can still start
├─ S4 another run active ──────────────────▶ launch locked; link to the active run
└─ S5 bad upload (name, exists) ───────────▶ error shown; rest continue
RUNNING
├─ R1 watch: live graph, metrics, logs
├─ R2 inspect any node: facts, findings, artifacts, error
├─ R3 stop the run ────────────────────────▶ status stopped
├─ A  node needs approval (theme, framework, curator)
│   ├─ A1 approve          ├─ A2 reject
│   ├─ A3 free-text answer (rename, refine) ─▶ node re-plans, may ask again
│   ├─ A4 answered on Telegram ──────────────▶ console reflects it
│   └─ A5 unknown curator action (ALREADY_PROCESSED) ─▶ asks instead of crashing
└─ E  node raised after its retries
    ├─ E1 recoverable: retry ─ succeeds / fails again and asks again (E2)
    ├─ E3 recoverable: abort ────────────────▶ failed
    ├─ E4 non-recoverable: only abort offered, reason shown
    ├─ E5 no answer before the reminder budget ▶ failed
    └─ E6 run waits under an old release with no pending.json ▶ reason shown, stop offered
ENDED, NOT OK
├─ F1 failed: reason + node shown ─▶ retry from checkpoint
├─ F2 crashed (dead pid) ─▶ reason shown; retry from checkpoint
├─ F3 stopped ─▶ retry from checkpoint
├─ F4 any of those ─▶ restart from scratch with the same input
├─ F5 input file gone ─▶ restart refused with reason
├─ F6 no checkpoint ─▶ retry refused with reason
└─ F7 dismiss from "needs you"
ENDED, OK
├─ D1 verdict PASS
├─ D2 verdict PARTIAL/FAIL ─▶ failing checks listed; output still usable
├─ D3 read every output in place: thumbnail, blog, KB entry, research, audit, artifacts
├─ D4 edit blog/KB/any text artifact in place, save
├─ D5 open research: http link / local book / no link
└─ D6 cost per node and run shown (API-equivalent under subscription)
PUBLISH (target = youtube | wordpress)
├─ P1 mode off ─▶ reason shown, no action
├─ P2 no package, can prepare ─▶ prepare, then P3 (older runs)
├─ P3 pending ─▶ edit fields, save without publishing
├─ P4 pending ─▶ publish; publishing; published with link
├─ P5 publish failed ─▶ error shown, edit, publish again
├─ P6 published ─▶ read-only with link
├─ P7 cannot prepare (no video, no blog post) ─▶ reason shown
├─ P8 validation error ─▶ per-field error
├─ P9 another publish holds the lock ─▶ error shown
├─ P10 auto mode ─▶ shows published or failed, no action unless failed
└─ P11 publisher died ─▶ failed, publish again
NAVIGATION
├─ N1 needs-you bar lists every run wanting a decision; one click to it
├─ N2 deep links: #/runs/<id>/{graph,output,logs}, reload keeps the view
├─ N3 output: section nav, keyboard, no long scroll, full width
├─ N4 graph: zoom, pan, fit; panel never hides it
└─ N5 backend unreachable ─▶ badge
```

## 3. Backend contracts

### C11 `pending.json` and `reply.json` (run dir)

`pending.json`, written atomically by the runner before it waits, removed when it stops waiting:

```json
{"id": "<uuid>", "kind": "approval|error", "node": "kb_curator", "title": "New theme: approval needed",
 "prompt": "<full text, the same one Telegram gets>", "error": "RuntimeError: …",
 "non_recoverable": "<reason>|null",
 "options": [{"reply": "yes", "label": "Approve", "style": "primary"}],
 "free_text": true, "created_at": 1793886800.0}
```

- `kind=approval`: options Approve (`yes`), Reject (`no`), `free_text: true`.
- `kind=error`, recoverable: Retry (`retry`), Abort (`abort`), `free_text: false`.
- `kind=error`, non-recoverable: only Abort.

`reply.json`: `{"id": "<pending id>", "reply": "<text>", "source": "console", "ts": …}`, written by the server with an atomic create. Runner ignores and removes a reply whose id differs.

### `pipeline/hitl.py` (stdlib + `pipeline.telegram`)

`ask(run_dir, request, reminder_interval, max_reminders=None) -> Optional[str]`: writes `pending.json`, starts Telegram ask as a killable subprocess (`telegram.ask_process`), polls `reply.json` every second, returns the first answer. On a console answer it kills the Telegram child so it cannot swallow the user's next message. Re-posts each `reminder_interval`. `None` when `max_reminders` is exhausted. Always removes `pending.json` and `reply.json` on exit. A Telegram failure must not stop the console wait (no token, no network). `run.py` and `retry.py` both use it. Both keep their current semantics otherwise, and the second failure after a retry must re-enter the prompt loop (today `graph.invoke(None)` runs inside the `except`, so it escapes).

### kb_curator

A plan whose action is neither `PROCEED` nor `NEEDS_APPROVAL*` raises a `NEEDS_APPROVAL`-style `interrupt` with `kind: "curator"`, showing action, reason and raw plan. The reply enters `approval_history`, so the curator re-plans (bounded as the loop is today by the user answering). The prompt in `pipeline/agents/kb-curator.md` is not changed unless a test shows it is needed.

### Run detail additions (`GET /api/runs/{id}`)

- `pending`: C11 or `null`. Only when the run is active.
- `failure`: `{"node": str|null, "error": str, "hint": str|null}` or `null`. From `run.json.error`, else the last node error. Set for failed/crashed/stopped, and for `waiting_reply` without `pending.json` (E6).
- `actions`: subset of `["reply", "stop", "retry", "restart", "dismiss"]` plus `action_blockers: {"retry": "<reason>", "restart": "<reason>"}` for refused ones (F5, F6, active run).
- `dismissed`: bool.
- `telemetry`: `cost_usd` and each `per_node[*].cost_usd` are API-equivalent when the logged cost is 0, computed at read time, so older runs are fixed too. Adds `cost_estimated: bool` at both levels. Model comes from usage lines too, so kb_curator rows without a model still price.
- Status `stopped` joins `FINISHED`.

### Run list additions (`GET /api/runs`)

Each item adds `cost_usd`, `cost_estimated`, `tokens` (total), `failed_node`, `attention: [{"kind": "approval|error|failed|crashed|stopped|publish", "label": str}]`. Empty when dismissed. `publish` means a package is `pending` or `failed`.

### New routes

| Route | Result |
|---|---|
| `POST /api/runs/{id}/reply` `{"id","reply"}` | 202. 404 unknown run. 409 no pending or stale id. 400 empty reply, or free text where `free_text` is false and the reply is not an option. |
| `POST /api/runs/{id}/stop` | 202. SIGTERM to the process group, SIGKILL after 5 s, then `status=stopped`, `ended_at`, removes `pending.json`. 409 not active. Only signals when the pid's cmdline contains `pipeline.` (guards pid reuse). |
| `POST /api/runs/{id}/retry` `{"auto_approve":false}` | 202 `{"run_id"}`. Spawns `pipeline.retry --run-id`. 409 active run (`run_id`), or `{"detail": blocker}` when not resumable. |
| `POST /api/runs/{id}/restart` `{"auto_approve":false}` | 201 `{"run_id": new}`. 404 `{"detail"}` input gone. 409 active run. |
| `POST /api/runs/{id}/dismiss` `{"dismissed":true}` | 200. Sets the flag in `run.json`. |
| `GET /api/runs/{id}/log?tail=500` | `{"lines":[…],"bytes":n}` from `stdout.log`. Empty when none. |
| `PUT /api/runs/{id}/artifacts/{aid}` raw body | 200 `{"bytes":n}`. Text kinds only (else 415), UTF-8, capped at `TEXT_CAP` (413), atomic. 409 while the run is active. |
| `GET /api/runs/{id}/research` | `{"items":[{"n","title","type","author","location","category","angle","relevance","paywall","theme","reachable","reason","link":{"kind":"http|local|none","href":str|null}}]}` from `research_report.csv`. |
| `GET /api/runs/{id}/research/{n}/file` | Serves a local item only when it resolves inside `Settings.books_root` (default `STATE_ROOT/books`; env `PIPELINE_UI_BOOKS_ROOT`). 404 otherwise. Path traversal and symlinks out are refused. |
| `PUT /api/runs/{id}/publish/{target}` `{"fields"}` | 200 package. Saves without publishing. Validates types and max, not `required`. 409 published or publishing. |
| `POST /api/runs/{id}/publish/{target}/prepare` | 201 package, built from the run's files. 409 exists or run active. 422 `{"detail"}` when it cannot (P1, P7). |
| `GET /api/runs/{id}/publish/{target}/asset/{role}` | Serves a package asset (only paths the package lists), range requests for video. 404 otherwise. |

`GET /api/runs/{id}/publish` becomes `{"targets":{…},"prepare":{"youtube":{"can":bool,"reason":str|null,"mode":"off|manual|auto"},"wordpress":{…}}}`. `targets` is always present. **This changes the old `{}` empty shape; update its tests.**

`GET /api/inputs` items add `previous_runs: [run_id]` (S3).

`GET /api/runs/{id}/result`: when the run is not ended it behaves as today.

Prepare builds from: WordPress `wordpress-draft/{post.md,meta.json,featured.*}` falling back to `painforwisdom-writer/blog_post.md`; YouTube `youtube-upload/metadata.json` and `input.path` or `<run>/source/*`, 422 when no video exists. Mode comes from `publishing_config.load(profile)`; `off` is 422. `FIELD_SPECS` moves to `pipeline/publish/specs.py` (stdlib only), because the node modules import clients the server must not import; the nodes import them from there.

`publishing_config.CONFIG_PATH` honours env `PIPELINE_PUBLISHING_CONFIG` (tests only).

### Pricing

`config/pricing.json` (catalog, per MTok: `input`, `output`, `cache_read`, `cache_write`, plus `as_of`, `source`). `pipeline/pricing.py` (stdlib): `cost(model, input, output, cache_read, cache_write) -> Optional[float]`, `None` for an unknown model (the UI then shows tokens only, never a fake 0). `cost_forecast.PRICES` reads it, so one table exists. `llm.py` keeps logging actual billed cost.

### Fake runner

Scenarios add `error` (waits on C11 at `research`; `retry` succeeds, `abort` fails), `error_twice`, `non_recoverable`, `curator` (kind `curator` at `kb_curator`), and `hitl` now waits on C11 instead of sleeping. An unanswered wait has a bounded timeout (`--wait-s`, default 120) and then fails. `fake_runner retry --run-id …` finishes the remaining nodes. `settings.runner == "fake"` selects it in `/retry`.

## 4. Frontend contracts

No new runtime dependencies, plain ES modules. **Text labels on every control**: icon-only is removed, it is why the UI can't be operated at a glance. `.dag` may hold `<text>`.

Route: `#/runs/<id>/<view>`, view in `graph|output|logs`, default `graph`. `app.js` owns it.

C7 additions: `view:changed` bus event (`{view}`).

```
┌ header: logo version profile · [needs-you n] ·················· [Start run] ┐
├ runs list ┬ run header: status, input, reason, primary actions ┬ inspector ┤
│ (text:    │ tabs: Graph | Output | Logs                        │ (docked,  │
│ input,    ├───────────────────────────────────────────────────┤  resizes, │
│ status,   │ active view, uses all the width                   │  graph    │
│ time,     │                                                   │  reflows) │
│ cost)     ├───────────────────────────────────────────────────┤           │
│           │ metrics bar: one line; expands to a table         │           │
└───────────┴───────────────────────────────────────────────────┴───────────┘
```

- **Action banner** (`js/actions.js`): at the top of the run. `pending`: title, prompt rendered, option buttons, free-text box with send. `failure`: node, error, hint. Buttons from `actions`, blockers as disabled + reason. Testids: `action-banner`, `action-title`, `action-prompt`, `action-option-<reply>`, `action-text`, `action-send`, `action-stop`, `action-retry`, `action-restart`, `action-dismiss`, `action-blocker-<verb>`, `failure-node`, `failure-error`. Stop and restart ask for a two-step confirm in place, never a browser dialog.
- **Needs-you** (`js/attention.js`): testids `needs-you`, `needs-you-count`, `needs-you-item-<run_id>`.
- **Graph** (`js/dag.js`): visible node labels; wheel zoom at the cursor, drag pan, buttons + keyboard `+ - 0`; `dag-zoom-in`, `dag-zoom-out`, `dag-fit`; `data-zoom` on the svg. Re-fits when its box resizes unless the user moved it. A click on a node after a drag does not select.
- **Inspector** (`js/drawer.js`): docked grid column. Shows `node.error` (`drawer-error`). Graph is never overlapped: asserted by bounding boxes.
- **Metrics bar** (`js/telemetry.js` absorbs `debug.js`): one line, `metrics-bar`, tokens, cache, cost (`≈` when estimated, tooltip explains), time; `metrics-toggle` expands a table (`metrics-table`, row `metrics-row-<node>`) sorted by tokens with state, time, model, tokens, cost and a small time bar. Clicking a row selects the node. Collapsed height ≤ 40 px. Remembered in `localStorage` with try/catch.
- **Logs** (`js/logs.js`): tail, follows while active. `logs`, `logs-follow`.
- **Output** (`js/operability.js` plus `js/output/*`): full width. Left section nav (`output-nav-<section>`, sections `summary, blog, youtube, wordpress, kb, research, audit, files`), keys `j/k`, one section visible at a time, no page-long scroll. Blog and package editors in side-by-side panes (edit, preview). Save (`save-<target>`), publish (`publish-<target>`), prepare (`prepare-<target>`), mode-off and cannot-prepare reasons (`prepare-reason-<target>`). YouTube section plays the video (`youtube-video`). Research is a table with filter, every row expands, and `research-link-<n>` opens http or the local-file route; `research-nolink-<n>` when neither. Artifact editors: `artifact-edit-<name>`, `artifact-save-<name>`. Dirty state is kept across polls, and leaving warns inline.

Keep existing testids where the behaviour is unchanged; rewrite specs where this spec changes it. The 100% JS coverage gate stays, measured across all shards.

## 5. Tests and CI

| Level | Where | How |
|---|---|---|
| Unit | `tests/unit_*` or `tests/test_*` with no server/subprocess | Pure logic |
| Integration | `@pytest.mark.integration`: FastAPI `TestClient`, real fake-runner subprocess | Routes, runner, mailbox |
| Browser (UAT) | `pipeline/ui/e2e/tests/` and `tests/uat/*.spec.js`, one flow per decision-tree branch | Fake runner |

CI jobs, each short and independently re-runnable, all PR checks:

- `pipeline unit (core|console)`, `pipeline integration`
- `console browser (1..N)` via `playwright --shard`, each uploading raw coverage
- `console coverage gate`: merges shard coverage, enforces 100%
- `decision tree`: `tests/test_decision_tree.py`
- Coach groups unchanged.
