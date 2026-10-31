# Coach release process

Same model as Jarvis OS `scripts/shipit.sh`: code reaches `main` only through a
reviewed PR; shipping cuts a release from a commit already on `origin/main`,
bumps the version on its own release branch (main is never bumped), and deploys
from origin.

## Concepts

- **Tag:** `vX.Y.Z`. The next version derives from the latest `v*` tag, not from
  `services/coach/pyproject.toml` on main.
- **Release branch:** `release/X.Y.Z`, one per version, cut from the shipped
  commit. It carries a single extra commit: the pyproject version bump, which the
  tag points at. `deploy-live.sh` refuses a tag not reachable from an
  `origin/release/*` branch (the **G4 gate**).
- **LIVE_DIR:** a stable checkout the prod stack runs from, decoupled from any
  dev worktree. Default `$HOME/workspace/painforwisdom-live` (override with
  `COACH_LIVE_DIR`). Holds the gitignored `services/coach/.env.coach`.
- **Durable state survives deploys:** vault RAG index, mem0 postgres, user
  memories (docker volumes), the host allowlist (`COACH_ACCESS_HOST_PATH`,
  default `/home/gonzalo/.coach-state/access.json`) and the vault bind
  (`VAULT_HOST_PATH`). The `SessionMap` is in-memory by design — no snapshot.

## Ship a new version

Land the change on `main` via PR, then from a clean checkout of `origin/main`:

```bash
services/coach/scripts/ship-it.sh                  # patch bump (v0.1.15 -> v0.1.16)
services/coach/scripts/ship-it.sh minor            # or major, or an explicit 0.2.0
services/coach/scripts/ship-it.sh --base <40-char sha>  # ship that origin/main commit
services/coach/scripts/ship-it.sh --dry-run        # print the plan, change nothing
```

Or the `/coach-ship-it` skill. `ship-it.sh` cuts `release/X.Y.Z`, bumps and tags
it in a throwaway worktree, pushes branch + tag, publishes a GitHub release with
the commits since the previous tag (best-effort), runs `deploy-live.sh vX.Y.Z`,
and sends a Telegram COMPLETE/FAILED alert via `notify_telegram.sh` (reads
`TELEGRAM_COACH_BOT_TOKEN` + `TELEGRAM_COACH_ALERT_CHAT_ID`; silent no-op if
unset). Dirty `OPERATION.md`, `ASSUMPTIONS.md`, `.claude/settings.json` and
`.serena/project.yml` do not block; any other tracked change does.

## Deploy an existing tag

```bash
services/coach/scripts/deploy-live.sh v0.1.16
```

Fetches the tag into `COACH_LIVE_DIR`, checks the G4 gate, `docker compose build
&& up -d` (project `coach`), waits for `/health`. Use this, never `ship-it.sh`,
when a ship died after its tag was pushed.

Emergency rollback to a pre-release tag: `DEPLOY_FORCE_LEGACY=1 deploy-live.sh <tag>`.
`COACH_NOTIFY_DRY_RUN=1` makes the notifier print instead of calling Telegram.
