#!/usr/bin/env bash
# ship-it.sh <vX.Y.Z>
#
# Tag a coach release from the tip of its release/X.Y.x branch, push it, and
# deploy it to the live docker stack via deploy-live.sh. Mirrors
# auto_heycrypto/scripts/ship-it.sh, minus the trading-session snapshot/restore
# (the coach has no durable per-user session state — see RELEASE.md).
#
#   Usage: ./scripts/ship-it.sh v0.1.3
#
# Gates (refuse-by-default):
#   G1  tag is strict semver vX.Y.Z
#   lock  only one ship-it at a time (/tmp/coach-ship-it.lock)
#   G2  release/ prefix requires a GitHub remote
#   G3  release/X.Y.x must exist on the remote
#   G4  the commit being tagged must be reachable from release/X.Y.x
#   V   pyproject.toml version at that commit must equal the tag (minus the 'v')
#   T   tag must not already exist (local or remote)
#
# Env overrides (defaults preserve production behaviour):
#   COACH_SHIPIT_REMOTE          origin
#   COACH_SHIPIT_RELEASE_PREFIX  release/
#   COACH_SHIPIT_DEPLOY_OVERRIDE <scriptdir>/deploy-live.sh
#   COACH_SHIPIT_NOTIFY_OVERRIDE <scriptdir>/notify_telegram.sh
#   COACH_SHIPIT_LOCK_PATH       /tmp/coach-ship-it.lock
#   COACH_SHIPIT_NOPUSH=1        keep tag local, skip push (testing)
#   COACH_SHIPIT_DRY_RUN=1       exit cleanly after G1/G2 + lock (testing)

set -euo pipefail

RED='\033[0;31m'; YELLOW='\033[1;33m'; GREEN='\033[0;32m'; NC='\033[0m'
info()  { echo -e "${GREEN}[ship-it]${NC} $*"; }
warn()  { echo -e "${YELLOW}[warn]${NC}    $*"; }
error() { echo -e "${RED}[error]${NC}   $*" >&2; }

TAG="${1:-}"
if [[ -z "$TAG" ]]; then
    error "Usage: $0 <vX.Y.Z>   (e.g. $0 v0.1.3)"
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
DEPLOY_SCRIPT="${COACH_SHIPIT_DEPLOY_OVERRIDE:-$SCRIPT_DIR/deploy-live.sh}"
TELEGRAM_SCRIPT="${COACH_SHIPIT_NOTIFY_OVERRIDE:-$SCRIPT_DIR/notify_telegram.sh}"
REMOTE="${COACH_SHIPIT_REMOTE:-origin}"
RELEASE_PREFIX="${COACH_SHIPIT_RELEASE_PREFIX:-release/}"
NOPUSH="${COACH_SHIPIT_NOPUSH:-0}"
LOCK_PATH="${COACH_SHIPIT_LOCK_PATH:-/tmp/coach-ship-it.lock}"
LOCK_STALE_MIN=30

notify() { [[ -x "$TELEGRAM_SCRIPT" ]] && "$TELEGRAM_SCRIPT" "$1" || true; }

# --- G1: strict semver -----------------------------------------------------
if [[ ! "$TAG" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    error "G1 FAIL: tag '$TAG' is not strict semver vX.Y.Z."
    exit 1
fi

# --- lock ------------------------------------------------------------------
_maybe_clear_stale_lock() {
    [[ -f "$LOCK_PATH" ]] || return 0
    local pid; pid="$(head -n1 "$LOCK_PATH" 2>/dev/null | tr -dc '0-9' || true)"
    [[ -z "$pid" ]] && return 0
    kill -0 "$pid" 2>/dev/null && return 0
    local now ts age; now="$(date +%s)"; ts="$(stat -c %Y "$LOCK_PATH" 2>/dev/null || echo "$now")"
    age=$(( (now - ts) / 60 ))
    if (( age >= LOCK_STALE_MIN )); then
        warn "Clearing stale lock $LOCK_PATH (pid=$pid dead, age=${age}m)."
        rm -f "$LOCK_PATH" || true
    fi
}
_maybe_clear_stale_lock
exec 9>"$LOCK_PATH"
if ! flock -n 9; then
    error "ship-it REFUSED: another ship-it holds $LOCK_PATH."
    notify "<b>ship-it $TAG REFUSED</b>%0AAnother ship-it is already in flight (lock held)."
    exit 99
fi
echo "$$" >&9

# --- G2: remote URL pin ----------------------------------------------------
REMOTE_URL="$(git remote get-url "$REMOTE" 2>/dev/null || true)"
if [[ "$RELEASE_PREFIX" == "release/" ]]; then
    if [[ ! "$REMOTE_URL" =~ ^(git@github\.com:|https://github\.com/) ]]; then
        error "G2 FAIL: release/ prefix requires \$REMOTE to point at GitHub (got '$REMOTE_URL')."
        exit 1
    fi
fi

# --- test dry-run exit (after G1/G2 + lock) --------------------------------
if [[ "${COACH_SHIPIT_DRY_RUN:-0}" == "1" ]]; then
    info "COACH_SHIPIT_DRY_RUN_OK -- lock acquired, gates G1/G2 passed; exiting before git ops."
    exit 0
fi

# --- G3: release branch exists ---------------------------------------------
[[ "$TAG" =~ ^v([0-9]+)\.([0-9]+)\.[0-9]+$ ]]
MINOR="${BASH_REMATCH[1]}.${BASH_REMATCH[2]}"
RELEASE_BRANCH="${RELEASE_PREFIX}${MINOR}.x"

if [[ "$NOPUSH" != "1" ]]; then
    info "Fetching $REMOTE (tags + branches)..."
    git fetch --tags --prune "$REMOTE"
fi

if ! git rev-parse -q --verify "refs/remotes/$REMOTE/$RELEASE_BRANCH" >/dev/null; then
    error "G3 FAIL: $RELEASE_BRANCH not found on $REMOTE."
    error "  Cut it first: $SCRIPT_DIR/cut-release.sh $MINOR"
    exit 1
fi
TARGET_SHA="$(git rev-parse "refs/remotes/$REMOTE/$RELEASE_BRANCH")"

# --- G4: reachability ------------------------------------------------------
if ! git branch -r --contains "$TARGET_SHA" | grep -qE "^[[:space:]]*$REMOTE/${RELEASE_PREFIX}"; then
    error "G4 FAIL: $TARGET_SHA not reachable from any $REMOTE/${RELEASE_PREFIX}* branch."
    exit 1
fi

# --- V: pyproject version must equal the tag -------------------------------
WANT_VERSION="${TAG#v}"
GOT_VERSION="$(git show "$TARGET_SHA:services/coach/pyproject.toml" 2>/dev/null \
    | sed -n 's/^version = "\(.*\)"/\1/p' | head -n1)"
if [[ "$GOT_VERSION" != "$WANT_VERSION" ]]; then
    error "V FAIL: services/coach/pyproject.toml version at $RELEASE_BRANCH is '$GOT_VERSION', tag wants '$WANT_VERSION'."
    error "  Bump the version on $RELEASE_BRANCH (via PR to main, then forward) before tagging."
    exit 1
fi

# --- T: tag must not already exist -----------------------------------------
if git rev-parse -q --verify "refs/tags/$TAG" >/dev/null; then
    error "T FAIL: tag '$TAG' already exists locally. Refusing to overwrite."
    exit 1
fi
if [[ "$NOPUSH" != "1" ]] && git ls-remote --exit-code --tags "$REMOTE" "refs/tags/$TAG" >/dev/null 2>&1; then
    error "T FAIL: tag '$TAG' already exists on $REMOTE. Refusing to overwrite."
    exit 1
fi

[[ -x "$DEPLOY_SCRIPT" ]] || { error "Deploy script not found/executable: $DEPLOY_SCRIPT"; exit 1; }

cd "$REPO_DIR"

# --- create + push tag -----------------------------------------------------
SHORT_SHA="$(git rev-parse --short "$TARGET_SHA")"
info "Tagging $RELEASE_BRANCH @ $SHORT_SHA as $TAG"
git tag -a "$TAG" -m "$TAG: shipped via ship-it.sh from $RELEASE_BRANCH@$SHORT_SHA" "$TARGET_SHA"

if [[ "$NOPUSH" == "1" ]]; then
    info "COACH_SHIPIT_NOPUSH=1: tag kept local, skipping push."
else
    info "Pushing tag to $REMOTE..."
    if ! git push "$REMOTE" "$TAG"; then
        error "Push failed. Removing local tag to keep state clean."
        git tag -d "$TAG" || true
        exit 1
    fi
    info "Tag '$TAG' pushed."
fi

# --- deploy ----------------------------------------------------------------
info "Invoking deploy-live.sh $TAG..."
if ! "$DEPLOY_SCRIPT" "$TAG"; then
    notify "<b>ship-it $TAG FAILED</b>%0Adeploy-live.sh exited non-zero. Live coach may be down."
    error "deploy-live.sh failed."
    exit 1
fi

info "--- ship-it complete ---"
info "Tag: $TAG  ->  deployed to the live coach stack."
notify "<b>ship-it $TAG COMPLETE</b>%0ACoach deployed from $RELEASE_BRANCH@$SHORT_SHA%0ATime: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
exit 0
