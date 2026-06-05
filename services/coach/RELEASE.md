# Coach release process

Mirrors the `auto_heycrypto` model (separate release branch per minor version,
semver tags, scripted deploy to a stable live checkout), adapted to the coach's
docker-compose stack.

## Concepts

- **Release branch:** `release/X.Y.x`, cut from `origin/main` once per minor
  version (e.g. `release/0.1.x`). Patches for a shipped line land here.
- **Tag:** `vX.Y.Z` (matches `services/coach/pyproject.toml` `version`). A tag is
  only deployable if it is reachable from its `release/X.Y.x` branch (the **G4
  gate** in `deploy-live.sh`).
- **LIVE_DIR:** a stable checkout the prod stack runs from, decoupled from any
  dev worktree. Default `$HOME/workspace/painforwisdom-live` (override with
  `COACH_LIVE_DIR`). Holds the gitignored `services/coach/.env.coach`.
- **Durable state survives deploys:** vault RAG index, mem0 postgres, user
  memories (docker volumes), the host allowlist (`COACH_ACCESS_HOST_PATH`,
  default `/home/gonzalo/.coach-state/access.json`) and the vault bind
  (`VAULT_HOST_PATH`). The `SessionMap` is in-memory by design — no snapshot.

## Deploy a tag

```bash
services/coach/scripts/deploy-live.sh v0.1.2
```

Fetches the tag into `COACH_LIVE_DIR`, checks the G4 gate, `docker compose build
&& up -d` (project `coach`), waits for `/health`.

Emergency rollback to a pre-release tag: `DEPLOY_FORCE_LEGACY=1 deploy-live.sh <tag>`.

## Cut a new release line (manual until cut-release.sh lands)

```bash
git fetch origin
git branch release/0.1.x origin/main
git push origin release/0.1.x
# then protect the branch (require PR review) via the GitHub API / settings
```

## Ship a new version (full flow — phase 2)

`cut-release.sh`, `ship-it.sh` and the `/coach-ship-it` skill (tag → deploy →
health-wait → Telegram notify, mirroring `/heycrypto-ship-it`) are the next
phase. Until then: bump `pyproject.toml`, PR to `main`, forward to
`release/X.Y.x`, `git tag vX.Y.Z` on the release tip, push, then run
`deploy-live.sh vX.Y.Z`.
