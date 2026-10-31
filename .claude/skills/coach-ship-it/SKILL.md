---
name: coach-ship-it
description: Ship a new coach version — cut release/X.Y.Z from merged origin/main, bump + tag vX.Y.Z on it, push, publish a GitHub release, deploy to the live docker stack via services/coach/scripts/ship-it.sh, notify Telegram. Use when the user says "ship the coach", "ship coach vX.Y.Z", "release the coach", "cut a coach release", or "deploy a new coach tag".
---

# coach-ship-it

Wrapper around `services/coach/scripts/ship-it.sh` (process: `services/coach/RELEASE.md`).
Same method as Jarvis OS `scripts/shipit.sh`.

Do NOT use to deploy an *existing* tag — run `services/coach/scripts/deploy-live.sh <tag>` directly.

## Argument: optional `patch` (default) | `minor` | `major` | `X.Y.Z`

The version derives from the latest `v*` tag; main's pyproject is never bumped.

## Pre-flight

1. The code must already be merged to `main`. Run from the dev checkout at
   `origin/main` (`git pull --ff-only`), or pass `--base <40-char sha>` of an
   `origin/main` commit.
2. Show the plan: `services/coach/scripts/ship-it.sh --dry-run [bump]`.
3. **Confirm.** "About to ship `<tag>` (branch `release/X.Y.Z`) and rebuild/restart
   the live coach stack. Confirm?" — wait for an explicit yes. It pushes a branch
   and tag to origin and restarts live containers.

## Execution

```bash
/home/gonzalo/workspace/painforwisdom/painforwisdom/services/coach/scripts/ship-it.sh [bump] [--base <sha>]
```

First turn after the restart is ~55s (cold reranker, once per process); the bot's
180s read timeout covers it.

## Error handling

- **Refused before pushing** (dirty tree, HEAD not origin/main, tag/branch exists,
  lock held): relay the message; nothing was published.
- **Deploy failure:** branch and tag are already on origin; do NOT delete them and
  never rerun ship-it. Fix the cause, then `deploy-live.sh <tag>`.
- **gh release failure:** non-fatal; the script prints the command to publish later.

## Post-run

Surface the tag shipped, the release page, the `deploy-live.sh` health result, and
`docker compose -p coach ps`.
