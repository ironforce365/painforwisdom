#!/usr/bin/env bash
# shipit.sh — tag an ALREADY-merged main commit as a pipeline release and deploy it.
# Named shipit.sh, not ship-it.sh, so the Jarvis release gate recognises it.
#
#   1. Clean tree; the shipped commit is on origin/main (HEAD itself without --base).
#      Version = next after the latest pipeline-vX.Y.Z tag; the first is 0.1.0.
#   2. Annotated tag pipeline-vX.Y.Z on that commit. No release branch: nothing to bump.
#   3. Push the tag, then publish a GitHub release (best-effort).
#   4. pipeline/scripts/deploy-live.sh: moves the live checkout, redeploys the console.
#   5. Notify Telegram (best-effort).
#
# Usage:
#   shipit.sh                  # patch bump from the latest pipeline-v* tag
#   shipit.sh X.Y.Z            # explicit version
#   shipit.sh patch|minor|major
#   shipit.sh --base <sha>     # ship THAT origin/main commit (full 40-char sha), not HEAD
#   shipit.sh --dry-run [...]  # print what would happen, change nothing
#
# Env overrides (tests):
#   PIPELINE_SHIPIT_DEPLOY_OVERRIDE  <scriptdir>/deploy-live.sh
#   PIPELINE_SHIPIT_NOTIFY_OVERRIDE  <repo>/telegram_io.sh
#   PIPELINE_SHIPIT_LOCK_PATH        /tmp/coach-ship-it.lock
set -euo pipefail

DRY_RUN=0
BASE_SHA=""
BUMP_OR_VERSION=""
while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY_RUN=1 ;;
    --base)    shift; BASE_SHA="${1:-}" ;;
    *) BUMP_OR_VERSION="$1" ;;
  esac
  shift
done

say() { printf '\033[1;36m▶ %s\033[0m\n' "$*"; }
run() { if [ "$DRY_RUN" = 1 ]; then printf '  [dry-run] %s\n' "$*"; else eval "$*"; fi; }
die() { printf '\033[1;31m✗ %s\033[0m\n' "$*" >&2; exit 1; }

# An abbreviation names one commit today and can name another later.
if [ -n "$BASE_SHA" ] && { [ "${#BASE_SHA}" != 40 ] || [ -n "${BASE_SHA//[0-9a-f]/}" ]; }; then
  die "--base takes a full 40-character sha, not '$BASE_SHA'"
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(git -C "$SCRIPT_DIR" rev-parse --show-toplevel)"
cd "$REPO"
DEPLOY_SCRIPT="${PIPELINE_SHIPIT_DEPLOY_OVERRIDE:-$SCRIPT_DIR/deploy-live.sh}"
NOTIFY_SCRIPT="${PIPELINE_SHIPIT_NOTIFY_OVERRIDE:-$REPO/telegram_io.sh}"
# The coach's lock on purpose: both ships move the same live checkout.
LOCK_PATH="${PIPELINE_SHIPIT_LOCK_PATH:-/tmp/coach-ship-it.lock}"

notify() { if [ "$DRY_RUN" = 1 ]; then printf '  [dry-run] notify: %s\n' "$1"; else
           [ -x "$NOTIFY_SCRIPT" ] && "$NOTIFY_SCRIPT" send "$1" || true; fi; }

exec 9>"$LOCK_PATH"
flock -n 9 || die "another ship holds $LOCK_PATH"

# --- 1. preconditions -----------------------------------------------------------
# Files Jarvis/Serena rewrite in managed checkouts: not authored edits, never a release input.
OS_MANAGED_PATHS=(OPERATION.md ASSUMPTIONS.md .claude/settings.json .serena/project.yml)
BLOCKING=()
while IFS= read -r p; do
  [ -n "$p" ] || continue
  listed=0
  for m in "${OS_MANAGED_PATHS[@]}"; do [ "$p" = "$m" ] && listed=1; done
  if [ "$listed" = 1 ]; then say "ignoring OS-managed dirty file: $p"; else BLOCKING+=("$p"); fi
done < <({ git -c core.quotePath=false diff --ignore-submodules=dirty --name-only
           git -c core.quotePath=false diff --ignore-submodules=dirty --cached --name-only; } | sort -u)
[ "${#BLOCKING[@]}" -eq 0 ] || die "tracked changes present — releases are cut from a clean, already-merged main:
$(printf '     %s\n' "${BLOCKING[@]}")"
[ -x "$DEPLOY_SCRIPT" ] || die "deploy script not found/executable: $DEPLOY_SCRIPT"

git remote get-url origin >/dev/null 2>&1 || die "no 'origin' remote — git is the source of truth for releases"
say "syncing with origin"
git fetch origin --tags --prune --quiet || die "cannot fetch origin — releases require the remote"
git rev-parse -q --verify refs/remotes/origin/main >/dev/null || die "origin/main not found"
if [ -n "$BASE_SHA" ]; then
  git cat-file -e "$BASE_SHA^{commit}" 2>/dev/null \
    || die "--base $BASE_SHA is not a commit in this checkout — fetch origin and pass a sha from origin/main"
  git merge-base --is-ancestor "$BASE_SHA" origin/main \
    || die "--base $BASE_SHA is not an ancestor of origin/main — releases are cut from already-merged main"
elif [ "$(git rev-parse HEAD)" != "$(git rev-parse origin/main)" ]; then
  die "HEAD is not origin/main — land the code on main via a merged PR and pull first
     (local $(git rev-parse --short HEAD) vs origin/main $(git rev-parse --short origin/main))"
fi

bump() {  # bump <X.Y.Z> <major|minor|patch>
  local IFS=.; read -r ma mi pa <<<"$1"
  case "$2" in
    major) echo "$((ma+1)).0.0" ;;
    minor) echo "$ma.$((mi+1)).0" ;;
    patch) echo "$ma.$mi.$((pa+1))" ;;
  esac
}
PREV="$(git tag -l 'pipeline-v*' | sed -E 's/^pipeline-v//' | { grep -E '^[0-9]+\.[0-9]+\.[0-9]+$' || true; } | sort -V | tail -1)"
case "$BUMP_OR_VERSION" in
  "")                if [ -n "$PREV" ]; then VERSION="$(bump "$PREV" patch)"; else VERSION=0.1.0; fi ;;
  major|minor|patch) VERSION="$(bump "${PREV:-0.0.0}" "$BUMP_OR_VERSION")" ;;
  *) [[ "$BUMP_OR_VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] \
       || die "invalid version/bump: '$BUMP_OR_VERSION' (want X.Y.Z or major|minor|patch)"
     VERSION="$BUMP_OR_VERSION" ;;
esac
TAG="pipeline-v$VERSION"
git rev-parse -q --verify "refs/tags/$TAG" >/dev/null && die "tag $TAG already exists"
git ls-remote --exit-code --tags origin "refs/tags/$TAG" >/dev/null 2>&1 && die "tag $TAG already exists on origin"

SHIP_SHA="$(git rev-parse "${BASE_SHA:-HEAD}")"
# First pipeline tag: notes start at the last shared v* release.
NOTES_FROM="${PREV:+pipeline-v$PREV}"
NOTES_FROM="${NOTES_FROM:-$(git tag -l 'v[0-9]*' --sort=-v:refname | sed -n 1p)}"
say "Releasing $TAG  (previous: ${NOTES_FROM:-none}, base: ${SHIP_SHA:0:9})"

# --- 2. tag ----------------------------------------------------------------------
run "git tag -a '$TAG' -m 'Pipeline $TAG' '$SHIP_SHA'"

# --- 3. push, then publish the GitHub release ------------------------------------
# Before deploying: the live checkout fetches from origin, so an unpushed tag cannot ship.
run "git push -q origin 'refs/tags/$TAG'"

release_notes() {
  printf "## What's changed\n\n"
  git log --first-parent --format='- %s (%h)' "${NOTES_FROM:+$NOTES_FROM..}$SHIP_SHA"
}
if ! command -v gh >/dev/null 2>&1; then
  say "gh not found — $TAG gets no release page; later: gh release create $TAG --generate-notes"
else
  NOTES_FILE="$(mktemp)"
  release_notes > "$NOTES_FILE"
  sed 's/^/    /' "$NOTES_FILE"
  if [ "$DRY_RUN" = 1 ]; then
    printf "  [dry-run] gh release create '%s' --title 'Pipeline %s' --notes-file <notes> --verify-tag\n" "$TAG" "$TAG"
  elif timeout 60 gh release create "$TAG" --title "Pipeline $TAG" --notes-file "$NOTES_FILE" --verify-tag; then
    say "release published for $TAG"
  else
    say "gh release create failed (non-fatal); later: gh release create $TAG --generate-notes"
  fi
  rm -f "$NOTES_FILE"
fi

# --- 4. deploy -------------------------------------------------------------------
say "deploying $TAG via $DEPLOY_SCRIPT"
if ! run "'$DEPLOY_SCRIPT' '$TAG'"; then
  notify "pipeline ship $TAG FAILED: deploy-live.sh exited non-zero. Tag is on origin; rerun pipeline/scripts/deploy-live.sh $TAG, not this ship."
  die "deploy-live.sh failed — rerun '$DEPLOY_SCRIPT $TAG' (the tag is already on origin)"
fi

# --- 5. notify -------------------------------------------------------------------
notify "pipeline ship $TAG COMPLETE (base ${SHIP_SHA:0:9})"
say "shipped $TAG"
