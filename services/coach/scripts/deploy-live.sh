#!/usr/bin/env bash
# deploy-live.sh — deploy a coach release tag to the live docker stack.
#
# Mirrors auto_heycrypto/scripts/deploy-live.sh, adapted for the coach's
# docker-compose deployment (heycrypto uses systemd + conda + streamlit).
#
#   Usage: deploy-live.sh <vX.Y.Z>
#
# Steps:
#   0. Refuse while a pipeline run is active in PFW_PROCESSED_ROOT (default
#      COACH_LIVE_DIR/processed). Override with DEPLOY_IGNORE_ACTIVE_RUN=1.
#   1. Fetch + checkout the tag (detached) in COACH_LIVE_DIR.
#   2. G4 gate: the tag must be reachable from a release/* branch — this
#      blocks deploying a tag that bypassed the release line. Override with
#      DEPLOY_FORCE_LEGACY=1 for an emergency rollback to a pre-release tag.
#   3. docker compose build + up -d (project "coach") from
#      COACH_LIVE_DIR/services/coach with --env-file .env.coach.
#   4. Wait for the agent /health to return 200.
#   5. Install the pipeline units from the new tag, restart the console, wait
#      for its /api/graph to return 200.
#
# DEPLOY_SKIP_COACH=1 ships the pipeline only: skips steps 3, 4 and the disk
# reclaim, leaving the coach containers as they are.
#
# Durable state — the vault RAG index, mem0 postgres, user memories (docker
# volumes), the host allowlist (COACH_ACCESS_HOST_PATH) and the vault bind
# (VAULT_HOST_PATH) — all persist across deploys. The coach SessionMap is
# in-memory by design, so there is no session snapshot/restore step (unlike
# heycrypto's live trading sessions).
set -euo pipefail

TAG="${1:?usage: deploy-live.sh <vX.Y.Z>}"
LIVE_DIR="${COACH_LIVE_DIR:-$HOME/workspace/painforwisdom-live}"
REMOTE="${COACH_DEPLOY_REMOTE:-origin}"
PROJECT="${COACH_COMPOSE_PROJECT:-coach}"
HEALTH_URL="${COACH_HEALTH_URL:-http://127.0.0.1:8800/health}"
RELEASE_PREFIX="${COACH_RELEASE_PREFIX:-origin/release/}"
PROCESSED_ROOT="${PFW_PROCESSED_ROOT:-$LIVE_DIR/processed}"
CONSOLE_UNIT=painforwisdom-console.service
CONSOLE_URL="http://127.0.0.1:8820/api/graph"

# G1: strict semver tag.
[[ "$TAG" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "G1 FAIL: bad tag '$TAG' (want vX.Y.Z)"; exit 1; }
[ -d "$LIVE_DIR/.git" ] || { echo "FAIL: COACH_LIVE_DIR is not a git checkout: $LIVE_DIR"; exit 1; }

# Prints the run_id of the first run.json with an active status and a live pid
# (same rule as pipeline/ui/runs.py; contract C2).
active_pipeline_run() {
  python3 - "$PROCESSED_ROOT" <<'PY'
import glob, json, os, sys

for path in sorted(glob.glob(os.path.join(sys.argv[1], "*", "langgraph", "run.json"))):
    try:
        with open(path) as f:
            rec = json.load(f)
        pid = rec["pid"]
        if rec["status"] not in ("running", "waiting_hitl", "waiting_reply") or not isinstance(pid, int) or pid <= 0:
            continue
        os.kill(pid, 0)
    except PermissionError:
        pass
    except (OSError, OverflowError, ValueError, KeyError, TypeError):
        continue
    print(path.split(os.sep)[-3])
    break
PY
}

# Agent prompts are read from disk at node time: a checkout mid-run would mix versions.
if [ "${DEPLOY_IGNORE_ACTIVE_RUN:-0}" != "1" ]; then
  ACTIVE_RUN="$(active_pipeline_run)"
  if [ -n "$ACTIVE_RUN" ]; then
    echo "pipeline run $ACTIVE_RUN active; retry after it ends"
    exit 1
  fi
fi

echo "==> fetch $REMOTE tags in $LIVE_DIR"
git -C "$LIVE_DIR" fetch --tags --prune "$REMOTE"

echo "==> checkout $TAG (detached)"
git -C "$LIVE_DIR" checkout --detach "tags/$TAG"

# G4: tag must be reachable from a release/ branch.
TAG_SHA="$(git -C "$LIVE_DIR" rev-parse "${TAG}^{commit}")"
if [ "${DEPLOY_FORCE_LEGACY:-0}" != "1" ]; then
  if ! git -C "$LIVE_DIR" branch -r --contains "$TAG_SHA" | grep -qE "^[[:space:]]*${RELEASE_PREFIX}"; then
    echo "G4 FAIL: $TAG ($TAG_SHA) is not reachable from a ${RELEASE_PREFIX}* branch."
    echo "         cut/forward the release branch first, or set DEPLOY_FORCE_LEGACY=1 (emergency only)."
    exit 1
  fi
fi

SKIP_COACH="${DEPLOY_SKIP_COACH:-0}"
COMPOSE_DIR="$LIVE_DIR/services/coach"
ENV_FILE="$COMPOSE_DIR/.env.coach"

dc() {
  docker compose -p "$PROJECT" --project-directory "$COMPOSE_DIR" \
    --env-file "$ENV_FILE" -f "$COMPOSE_DIR/docker-compose.yml" "$@"
}

if [ "$SKIP_COACH" != "1" ]; then
  [ -f "$ENV_FILE" ] || { echo "FAIL: missing $ENV_FILE (copy it from a configured checkout; it is gitignored)"; exit 1; }

  echo "==> build"
  dc build
  echo "==> up -d"
  dc up -d

  echo "==> wait for health ($HEALTH_URL)"
  for i in $(seq 1 45); do
    if curl -fsS -m 5 "$HEALTH_URL" >/dev/null 2>&1; then
      echo "healthy after $((i * 2))s"
      break
    fi
    if [ "$i" = 45 ]; then echo "FAIL: health timeout at $HEALTH_URL"; exit 1; fi
    sleep 2
  done
fi

echo "==> install pipeline units from $TAG"
PFW_LIVE_DIR="$LIVE_DIR" bash "$LIVE_DIR/pipeline/scripts/install-pipeline-units.sh" --no-restart
echo "==> restart $CONSOLE_UNIT"
systemctl --user restart "$CONSOLE_UNIT"

echo "==> wait for console ($CONSOLE_URL)"
CONSOLE_UP=0
SECONDS=0
for _ in $(seq 1 30); do
  if curl -fsS -m 2 "$CONSOLE_URL" >/dev/null 2>&1; then
    CONSOLE_UP=1
    echo "console up after ${SECONDS}s"
    break
  fi
  if [ "$SECONDS" -ge 30 ]; then break; fi
  sleep 1
done
if [ "$CONSOLE_UP" != 1 ]; then
  echo "FAIL: console timeout at $CONSOLE_URL (journalctl --user -u $CONSOLE_UNIT -n 50)"
  exit 1
fi

if [ "$SKIP_COACH" = "1" ]; then
  echo "==> deployed $TAG (pipeline only, coach untouched) from $LIVE_DIR"
  exit 0
fi

echo "==> deployed $TAG to project '$PROJECT' from $LIVE_DIR"
dc ps

# Reclaim disk — best-effort, and only AFTER health passed (a failed deploy exits
# above, keeping the old image as a rollback target). Each rebuild retags :latest
# and leaves the previous coach image (~11GB) dangling; without this they pile up
# silently (a prior ship died on a full disk). `until=168h` evicts only build
# cache unused for >7 days, preserving the active pip cache mount so rebuilds stay
# fast. Failures here never fail the deploy.
echo "==> reclaim disk: prune dangling images + stale build cache (best-effort)"
docker image prune -f || true
docker builder prune -f --filter until=168h || true
docker system df || true
