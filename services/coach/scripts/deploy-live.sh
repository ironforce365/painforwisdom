#!/usr/bin/env bash
# deploy-live.sh — deploy a coach release tag to the live docker stack.
#
# Mirrors auto_heycrypto/scripts/deploy-live.sh, adapted for the coach's
# docker-compose deployment (heycrypto uses systemd + conda + streamlit).
#
#   Usage: deploy-live.sh <vX.Y.Z>
#
# Steps:
#   1. Fetch + checkout the tag (detached) in COACH_LIVE_DIR.
#   2. G4 gate: the tag must be reachable from a release/X.Y.x branch — this
#      blocks deploying a tag that bypassed the release line. Override with
#      DEPLOY_FORCE_LEGACY=1 for an emergency rollback to a pre-release tag.
#   3. docker compose build + up -d (project "coach") from
#      COACH_LIVE_DIR/services/coach with --env-file .env.coach.
#   4. Wait for the agent /health to return 200.
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

# G1: strict semver tag.
[[ "$TAG" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "G1 FAIL: bad tag '$TAG' (want vX.Y.Z)"; exit 1; }
[ -d "$LIVE_DIR/.git" ] || { echo "FAIL: COACH_LIVE_DIR is not a git checkout: $LIVE_DIR"; exit 1; }

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

COMPOSE_DIR="$LIVE_DIR/services/coach"
ENV_FILE="$COMPOSE_DIR/.env.coach"
[ -f "$ENV_FILE" ] || { echo "FAIL: missing $ENV_FILE (copy it from a configured checkout; it is gitignored)"; exit 1; }

dc() {
  docker compose -p "$PROJECT" --project-directory "$COMPOSE_DIR" \
    --env-file "$ENV_FILE" -f "$COMPOSE_DIR/docker-compose.yml" "$@"
}

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
