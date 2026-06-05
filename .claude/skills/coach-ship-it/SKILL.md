---
name: coach-ship-it
description: Ship a new coach version — tag the tip of its release/X.Y.x branch and deploy it to the live docker stack via services/coach/scripts/ship-it.sh. Prompts for the tag if missing, cuts the release branch if needed, then deploys to LIVE_DIR and notifies Telegram. Use when the user says "ship the coach", "ship coach vX.Y.Z", "release the coach", "cut a coach release", or "deploy a new coach tag".
---

# coach-ship-it

Wrapper around `services/coach/scripts/ship-it.sh` that:
1. Creates an annotated tag on the tip of `release/X.Y.x` and pushes it.
2. Chains into `services/coach/scripts/deploy-live.sh` (checkout tag in
   `COACH_LIVE_DIR`, `docker compose build && up -d`, wait for `/health`).
3. Sends a Telegram COMPLETE/FAILED alert via `notify_telegram.sh`.

There is **no session snapshot/restore** (unlike heycrypto): the coach's
durable state — vault index, mem0 postgres, user memories (docker volumes), the
host allowlist, the vault bind — survives a deploy, and the `SessionMap` is
ephemeral by design.

## When to use

Trigger on: "ship the coach", "ship coach vX.Y.Z", "release the coach", "cut a
coach release", "deploy a new coach tag", or explicit `/coach-ship-it`.

Do NOT use to deploy an *existing* tag — run `services/coach/scripts/deploy-live.sh <tag>` directly.

## Required argument: `<tag>` (semver `vX.Y.Z`, must not exist yet)

If the user did not give a tag, show the latest first — do not guess or auto-bump:
```bash
REPO=/home/gonzalo/workspace/painforwisdom/painforwisdom
git -C "$REPO" fetch --tags --prune origin >/dev/null 2>&1
git -C "$REPO" tag --sort=-v:refname | head -5
```
Ask: "Latest coach tag is `vA.B.C`. Which tag do you want to ship?" Wait for the answer.

## Pre-flight

1. **Version match (the V gate).** `ship-it.sh` refuses unless
   `services/coach/pyproject.toml` `version` on the release branch equals the tag
   minus `v`. So before shipping `v0.1.3`, the version bump to `0.1.3` must already
   be merged to `main` and forwarded onto `release/0.1.x`. If it isn't, stop and
   tell the user to land the bump first (PR to main → forward to the release branch).
2. **Release branch exists (G3).** For `vX.Y.Z`, `release/X.Y.x` must exist on origin:
   ```bash
   git -C "$REPO" ls-remote --exit-code --heads origin "refs/heads/release/X.Y.x" >/dev/null 2>&1
   ```
   If missing, ask: "Cut `release/X.Y.x` from origin/main and apply branch protection? (yes/no)". If yes:
   ```bash
   "$REPO"/services/coach/scripts/cut-release.sh X.Y
   # then run the gh api branch-protection command it prints
   ```
   Refuse to proceed if the user declines.
3. **Confirm.** Show: "About to tag `release/X.Y.x` as `<tag>` and deploy to the
   live coach stack. Confirm?" — wait for an explicit yes. This pushes a tag to
   origin and rebuilds/restarts the live containers; it is not silent.

## Execution

```bash
/home/gonzalo/workspace/painforwisdom/painforwisdom/services/coach/scripts/ship-it.sh <tag>
```

End-to-end the script: validates G1 (semver) → acquires the lockfile → G2 (release/
needs a GitHub remote) → G3 (release branch exists) → G4 (tag commit reachable from
the release branch) → V (pyproject version == tag) → T (tag not already present) →
creates + pushes the annotated tag → runs `deploy-live.sh <tag>` (checkout in
`COACH_LIVE_DIR=$HOME/workspace/painforwisdom-live`, G4 re-check, `docker compose
build && up -d`, wait `/health`) → Telegram COMPLETE alert.

First turn after the restart is ~55s (cold reranker, once per process); the bot's
180s read timeout covers it.

## Error handling

- **G1/G2/G3/G4/V/T failure:** the script refuses before tagging — relay the
  specific gate message. For V, the fix is to land the version bump; for G3, cut
  the release branch.
- **Lock held (exit 99):** another ship-it is in flight — wait, don't force.
- **Tag exists:** pick the next semver; never delete/force a published tag.
- **Push failure:** the script already rolled back the local tag — relay stderr.
- **Deploy failure:** the tag is already published; do NOT delete it. A Telegram
  FAILED alert is sent. Report the `deploy-live.sh` failure; let the user decide
  to retry or cut a follow-up patch tag.

## Post-run

Surface from the output: the tag shipped, the `deploy-live.sh` health result, and
`docker compose -p coach ps`. Telegram alerts (COMPLETE/FAILED/REFUSED) go to
`TELEGRAM_COACH_ALERT_CHAT_ID`; if the token/chat aren't configured the notifier
silently no-ops and the ship still succeeds.

## Testing the gates without shipping

`COACH_SHIPIT_DRY_RUN=1 ship-it.sh vX.Y.Z` exits 0 after G1/G2 + lock.
`COACH_NOTIFY_DRY_RUN=1` makes `notify_telegram.sh` print instead of calling the API.
