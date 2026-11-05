#!/usr/bin/env bash
# deploy-live.sh — deploy a pipeline release tag to the live checkout. Never touches docker.
#
#   Usage: deploy-live.sh <pipeline-vX.Y.Z>
#
#   0. Refuse while a pipeline run is active (DEPLOY_IGNORE_ACTIVE_RUN=1 overrides).
#   1. Fetch; the tag must be reachable from origin/main (reviewed code only).
#   2. Checkout the tag (detached) in PFW_LIVE_DIR.
#   3. Install the pipeline units, restart the console, wait for /api/graph.
set -euo pipefail

TAG="${1:?usage: deploy-live.sh <pipeline-vX.Y.Z>}"
LIVE_DIR="${PFW_LIVE_DIR:-$HOME/workspace/painforwisdom-live}"
REMOTE="${PFW_DEPLOY_REMOTE:-origin}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=pipeline/scripts/deploy-lib.sh
. "$SCRIPT_DIR/deploy-lib.sh"

[[ "$TAG" =~ ^pipeline-v[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "FAIL: bad tag '$TAG' (want pipeline-vX.Y.Z)"; exit 1; }
[ -d "$LIVE_DIR/.git" ] || { echo "FAIL: PFW_LIVE_DIR is not a git checkout: $LIVE_DIR"; exit 1; }

refuse_active_run

echo "==> fetch $REMOTE in $LIVE_DIR"
git -C "$LIVE_DIR" fetch --tags --prune "$REMOTE"

TAG_SHA="$(git -C "$LIVE_DIR" rev-parse "refs/tags/$TAG^{commit}")"
if ! git -C "$LIVE_DIR" merge-base --is-ancestor "$TAG_SHA" "$REMOTE/main"; then
  echo "FAIL: $TAG ($TAG_SHA) is not reachable from $REMOTE/main; live checkout not moved."
  exit 1
fi

echo "==> checkout $TAG (detached)"
git -C "$LIVE_DIR" checkout --detach "tags/$TAG"

deploy_console "$TAG"

echo "==> deployed $TAG (coach untouched) from $LIVE_DIR"
