# Split painforwisdom into three Jarvis projects

Investigation for wo-5e58ef32. No code changed. Input for a later feature order.

## 1. Goal

Three Jarvis OS projects, each with its own configuration, gates, deploy, release, work orders, telemetry and knowledge entries:

| Project | Owns |
|---|---|
| `painforwisdom` | `pipeline/`, root `tests/`, `config/`, `data/`, `scripts/` |
| `pfw_coach` | `services/coach/` |
| `pfw_kb` | Obsidian vault (repo `gonandrap/painforwisdom-kb`) |

## 2. Findings

### 2.1 Coupling today

- Code: none. Coach never imports `pipeline`; pipeline never imports coach. Only 1 of the last 60 commits touched both (`#44`, the coach's first landing).
- Data contract: the vault file layout. Pipeline writes `gonzalo-book/` via `VAULT_PATH` (`pipeline/runtime.py:58`, default `<repo>/obsidian-vault`); `poc_brief_v2.py` git-commits into it. Coach mounts `VAULT_HOST_PATH` (`services/coach/.env.template:28`, the dev folder's submodule) at `/vault`, reads it through vault_rag + doctrine, writes `_inbox/<user>/` (sidecar).
- Shared external services: Notion DBs, Telegram bot/chat, Max OAuth.
- Release coupling (the real coupling):
  - One tag namespace `vX.Y.Z` for coach and augment (kn-f7ffb135).
  - One live folder `~/workspace/painforwisdom-live`: coach `deploy-live.sh` checks out the tag there, and the pipeline units run from it (`pipeline/systemd/*.service`). Shipping the coach silently moves pipeline code too.
  - `pipeline/scripts/install-pipeline-units.sh:24` tells you to ship a pipeline change with the coach's `ship-it.sh`.
- CI: one `.github/workflows/ci.yml`, no path filters; every PR runs both jobs.
- Jarvis: one catalog entry, one `max_concurrent=4`, one KB (27 entries, mixed topics), one cost ledger.

### 2.2 Jarvis constraint: a project path must be a repo root

`jarvis_os/src/jarvis/worker_session.py:56` looks for worktrees at `<project.path>/.claude/worktrees/<wt>`, and `session_dispatch.py:172` `harness_worktree_ok` requires each worktree to be its own `--show-toplevel`. Claude Code creates worktrees at the repo root's `.claude/worktrees/`. So `path: .../painforwisdom/services/coach` does not work without a Jarvis change. Each Jarvis project needs its own git checkout as its path.

## 3. Options

### A. One GitHub repo, one clone per Jarvis project (recommended)

`~/workspace/painforwisdom/painforwisdom` (pipeline, existing), `~/workspace/painforwisdom/pfw_coach` (second clone of `gonandrap/painforwisdom`), `~/workspace/painforwisdom/pfw_kb` (clone of `painforwisdom-kb`).

Pros
- No GitHub surgery, no history rewrite, no cross-repo dependency mechanism needed.
- Full OS granularity: catalog entry, gates, validation, `max_concurrent`, model/effort, KB, cost all per project.
- Reversible: offboard a project and the old layout still works.
- Keeps option B open: everything in section 5 is also required by B.

Cons
- Every worker still sees the whole monorepo. Scope is enforced by the catalog (`worker.append_system_prompt` + `settings_overrides.permissions.deny` on `Edit/Write` of other components), not by git.
- Root `CLAUDE.md` is shared by both monorepo projects; each needs a scoped one via `append_system_prompt` or a slimmer root file pointing to per-component docs.
- GitHub issues, PR list, Actions and releases stay mixed; separate them by labels, tag prefixes and CI path filters.
- Two dev folders of one repo: each must `git pull` main separately (kn-4a81fb2a item 4: dev main is read-only except pulls).

### B. Separate GitHub repos (`painforwisdom`, `pfw-coach`, `painforwisdom-kb`)

Extract `services/coach/` with `git filter-repo --subdirectory-filter` into a new repo.

Pros: isolation by git itself; own issues, Actions, releases, branch protection; smaller worktrees.
Cons: history rewrite, PR links in commits point to the old repo, two sets of secrets/branch rules, docs/specs need splitting (`docs/superpowers/specs/` is mixed). Benefit over A is only GitHub-side tidiness; OS granularity is identical.

Cross-repo interaction under B needs no submodule or package dependency between pipeline and coach: their only contract is the vault layout, which both read from a host path. Pipeline may keep `obsidian-vault` as a submodule or drop it for `VAULT_PATH`; coach must not take one (it only mounts a path).

### C. One repo, Jarvis supports subdirectory projects

Change Jarvis so a project can be `(repo, subdir)`: worktree root at the repo, cwd and CLAUDE.md resolution in the subdir. Cleanest end state, but it is a Jarvis OS feature (other repo, other project), not this one's. File it as a Jarvis issue if A's shared-root friction proves real.

### Recommendation

A now. Revisit B only if mixed GitHub issues/Actions become a pain; section 5 makes that move cheap.

## 4. Target architecture (option A)

### 4.1 Folders on host

| Path | What | Who writes |
|---|---|---|
| `~/workspace/painforwisdom/painforwisdom` | pipeline dev folder (Jarvis `painforwisdom`) | pulls only |
| `~/workspace/painforwisdom/pfw_coach` | coach dev folder (Jarvis `pfw_coach`) | pulls only |
| `~/workspace/painforwisdom/pfw_kb` | kb dev folder (Jarvis `pfw_kb`), branch `main` | pulls only |
| `~/workspace/painforwisdom-live` | rename to `~/workspace/pfw-coach-live`: coach tag checkout | `deploy-live.sh` |
| `~/workspace/pfw-pipeline-live` | new: pipeline tag checkout for systemd units | pipeline deploy script |
| `~/workspace/painforwisdom/state/` | `PAINFORWISDOM_STATE_ROOT`: `.env`, `books/`, `briefs/`, `reports/` | pipeline runtime |
| `~/workspace/painforwisdom/state/vault` | runtime vault: clone of `painforwisdom-kb` on `draft` | pipeline (entries, briefs), coach sidecar (`_inbox`) |

The runtime vault moves out of the pipeline dev folder's submodule so the coach no longer mounts a path inside another project's checkout. `VAULT_PATH` (pipeline) and `VAULT_HOST_PATH` (coach) both point at `state/vault`. `pfw_kb` work orders edit through PRs to `painforwisdom-kb`; the runtime vault pulls/merges.

Open point for the user: who promotes `draft` to `main` in `painforwisdom-kb` (today: manual review). Under `pfw_kb` that can become a work-order kind with its own gate.

### 4.2 Releases

- Tags: `pipeline-vX.Y.Z` and `coach-vX.Y.Z`. Existing `v0.1.15` stays as the coach's last legacy tag; `ship-it.sh` reads the latest `coach-v*` (falls back to `v*` once).
- Coach: `services/coach/scripts/ship-it.sh` + `deploy-live.sh` unchanged in shape, `COACH_LIVE_DIR` default `pfw-coach-live`, tag prefix `coach-`. Skill `/coach-ship-it` moves to `pfw_coach`.
- Pipeline: new `pipeline/scripts/ship-it.sh` (same Jarvis release method, tag `pipeline-v*`, checkout in `pfw-pipeline-live`, `install-pipeline-units.sh` repointed). Units' `WorkingDirectory` moves to `pfw-pipeline-live`.
- KB: no releases; `main` is the reviewed state.
- Jarvis `release` gate is then per project.

### 4.3 CI

`ci.yml` gets `paths:` filters (or two workflows): pipeline job on `pipeline/**`, `tests/**`, `config/**`, `data/**`; coach job on `services/coach/**`. Both on `.github/**`. `painforwisdom-kb` gets no CI unless `pfw_kb` wants link/frontmatter checks (skipped here).

### 4.4 Jarvis catalog entries

Copy the current `painforwisdom` entry for each, then scope:
- `painforwisdom`: deny `Edit/Write(services/coach/**)`; `append_system_prompt` names pipeline docs; `description` drops "coach service".
- `pfw_coach`: deny `Edit/Write(pipeline/**)`, `Edit/Write(tests/**)` (root tests); prompt points to `services/coach/README.md`; supervisor/health probes for the coach stack and watchdog move here.
- `pfw_kb`: small `max_concurrent`, `pr_merge` gate on (content review), no `service_restart`/`release`.
- Keep the shared push-to-main denies on all three.

### 4.5 Knowledge entries

Re-home the 27 `painforwisdom` entries: coach topics (`kn-a9ae1b7f`, `kn-e1b49f3a`, `kn-080d54ce`, `kn-775ab565`, coach part of `kn-447a3dad`, grounding roadmap `kn-5278d08d`) to `pfw_coach`; `kn-f9b7ae7f` to `pfw_kb`; user/feedback entries become global or are copied. `jarvis learn` has no move verb: re-add under the new project and retire the old entry with a pointer.

## 5. Work orders

Ordered; each is one PR (or one host/OS action). `[gate]` = privileged action, needs approval.

| # | Project | Work order | Depends on |
|---|---|---|---|
| 1 | painforwisdom | Tag namespaces: `ship-it.sh` reads/writes `coach-v*` with `v*` fallback; docs + KB updated. No deploy. | none |
| 2 | painforwisdom | CI path filters in `.github/workflows/ci.yml`. | none |
| 3 | painforwisdom | Split live folders: `COACH_LIVE_DIR` default `pfw-coach-live` in `deploy-live.sh`, `install-watchdog.sh`, `notify_telegram.sh`, `watchdog/runner.py:51`; pipeline units and `install-pipeline-units.sh` to `pfw-pipeline-live`. | 1 |
| 4 | painforwisdom | Pipeline release script `pipeline/scripts/ship-it.sh` (tag `pipeline-v*`, deploy to `pfw-pipeline-live`, reinstall units). | 1, 3 |
| 5 | painforwisdom | Runtime vault out of the submodule: `state/vault` clone on `draft`, `VAULT_PATH` via `.env`, coach `VAULT_HOST_PATH` in `.env.template`, `build_calibration_set.py:223` default. Host move is a manual step listed in the PR. | none |
| 6 | host [gate] | Create `pfw-coach-live` and `pfw-pipeline-live`, ship first `coach-v*` and `pipeline-v*`, reinstall watchdog + pipeline units, retire `painforwisdom-live`. | 3, 4, 5 |
| 7 | user | Clone `pfw_coach` and `pfw_kb` dev folders; add catalog entries (4.4); `jarvis adopt` each. Jarvis config, not this repo. | 6 |
| 8 | painforwisdom | Scope root `CLAUDE.md`/README to the monorepo split: per-component pointers, which project owns what; move `/coach-ship-it` skill note. | 7 |
| 9 | each project | Re-home knowledge entries (4.5). | 7 |
| 10 | pfw_kb | `pfw_kb` CLAUDE.md + operating notes (draft/main promotion, `_inbox` rules) in `painforwisdom-kb`. | 7 |

1, 2 and 5 can run in parallel. 6 and 7 are not code work orders: 6 needs `release` + `service_restart` gates, 7 is the user's catalog edit.

## 6. Risks

- Item 5 moves live data: coach must be stopped or the mount swapped while it is idle; take a copy of `obsidian-vault/` first, including unpushed `draft` commits and `_inbox` files.
- Item 6 is the first deploy from new folders: the pipeline timers are disabled today (kn-a9dd6331), so its risk is low; the coach bot is reported down since 2026-10-21, so fix that before or as part of item 6.
- Not checked: whether `jarvis adopt` on a second clone of the same remote confuses any Jarvis landing/merge detection (shared `origin/main`). Prove it on `pfw_coach` with one throwaway work order before item 8.
