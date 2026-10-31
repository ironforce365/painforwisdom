#!/usr/bin/env bash
# ship-it.sh — cut a coach release from ALREADY-merged main and deploy it to the live
# docker stack. Ports Jarvis OS scripts/shipit.sh (agentic_os); see RELEASE.md.
#
#   1. Clean tree; the shipped commit is on origin/main (HEAD itself without --base).
#      Version = next after the latest vX.Y.Z tag — main's pyproject is never bumped.
#   2. Cut release/X.Y.Z from that commit.
#   3. Bump services/coach/pyproject.toml + commit + annotated tag vX.Y.Z ON the release
#      branch, in a throwaway worktree so the shared checkout never moves.
#   4. Push branch + tag to origin, then publish a GitHub release whose notes list what
#      main gained since the previous tag (best-effort).
#   5. deploy-live.sh vX.Y.Z: LIVE_DIR fetches the tag from origin, docker compose
#      build + up -d, waits for /health.
#   6. Notify Telegram (best-effort).
#
# Usage:
#   ship-it.sh                  # patch bump from the latest v* tag
#   ship-it.sh X.Y.Z            # explicit version
#   ship-it.sh patch|minor|major
#   ship-it.sh --base <sha>     # ship THAT origin/main commit (full 40-char sha), not HEAD
#   ship-it.sh --dry-run [...]  # print what would happen, change nothing
#
# Env overrides (tests):
#   COACH_SHIPIT_DEPLOY_OVERRIDE  <scriptdir>/deploy-live.sh
#   COACH_SHIPIT_NOTIFY_OVERRIDE  <scriptdir>/notify_telegram.sh
#   COACH_SHIPIT_LOCK_PATH        /tmp/coach-ship-it.lock
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
DEPLOY_SCRIPT="${COACH_SHIPIT_DEPLOY_OVERRIDE:-$SCRIPT_DIR/deploy-live.sh}"
TELEGRAM_SCRIPT="${COACH_SHIPIT_NOTIFY_OVERRIDE:-$SCRIPT_DIR/notify_telegram.sh}"
LOCK_PATH="${COACH_SHIPIT_LOCK_PATH:-/tmp/coach-ship-it.lock}"
PYPROJECT="services/coach/pyproject.toml"

notify() { if [ "$DRY_RUN" = 1 ]; then printf '  [dry-run] notify: %s\n' "$1"; else
           [ -x "$TELEGRAM_SCRIPT" ] && "$TELEGRAM_SCRIPT" "$1" || true; fi; }

# --- lock: one ship at a time ---------------------------------------------------
exec 9>"$LOCK_PATH"
flock -n 9 || die "another ship-it holds $LOCK_PATH"

# --- 1. preconditions -----------------------------------------------------------
# Files Jarvis/Serena rewrite in managed checkouts: not authored edits, never a release
# input (release is cut from a sha and deployed from origin), so they do not block.
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

ORIGIN_URL="$(git remote get-url origin 2>/dev/null)" \
  || die "no 'origin' remote — git is the source of truth for releases"
say "syncing with origin ($ORIGIN_URL)"
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

latest_tagged() {  # highest X.Y.Z among vX.Y.Z tags, empty if none
  git tag -l 'v*' | sed -E 's/^v//' | { grep -E '^[0-9]+\.[0-9]+\.[0-9]+$' || true; } | sort -V | tail -1
}
bump() {  # bump <X.Y.Z> <major|minor|patch>
  local IFS=.; read -r ma mi pa <<<"$1"
  case "$2" in
    major) echo "$((ma+1)).0.0" ;;
    minor) echo "$ma.$((mi+1)).0" ;;
    patch) echo "$ma.$mi.$((pa+1))" ;;
  esac
}
PREV="$(latest_tagged)"
[ -n "$PREV" ] || PREV="$(sed -n 's/^version = "\(.*\)"/\1/p' "$PYPROJECT" | head -n1)"
case "$BUMP_OR_VERSION" in
  "")                VERSION="$(bump "$PREV" patch)" ;;
  major|minor|patch) VERSION="$(bump "$PREV" "$BUMP_OR_VERSION")" ;;
  *) [[ "$BUMP_OR_VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] \
       || die "invalid version/bump: '$BUMP_OR_VERSION' (want X.Y.Z or major|minor|patch)"
     VERSION="$BUMP_OR_VERSION" ;;
esac
TAG="v$VERSION"
REL_BRANCH="release/$VERSION"
git rev-parse -q --verify "refs/tags/$TAG" >/dev/null && die "tag $TAG already exists"
git ls-remote --exit-code --tags origin "refs/tags/$TAG" >/dev/null 2>&1 && die "tag $TAG already exists on origin"
git rev-parse -q --verify "refs/heads/$REL_BRANCH" >/dev/null && die "branch $REL_BRANCH already exists"

SHIP_SHA="$(git rev-parse "${BASE_SHA:-HEAD}")"
say "Releasing coach $VERSION  (previous: v$PREV, base: ${SHIP_SHA:0:9})"
say "  branch: $REL_BRANCH   tag: $TAG"

# --- 2. cut the release branch --------------------------------------------------
run "git branch '$REL_BRANCH' '$SHIP_SHA'"

# --- 3. bump + commit + tag on the release branch (main untouched) -------------
if [ "$DRY_RUN" = 1 ]; then WT="<tmp-worktree>"; else WT="$(mktemp -d)"; fi
cleanup() { [ "$DRY_RUN" != 1 ] && [ -d "${WT:-}" ] && git worktree remove --force "$WT" 2>/dev/null || true; }
trap cleanup EXIT
run "git worktree add --quiet '$WT' '$REL_BRANCH'"
# awk, not GNU-only `sed -i '0,/re/…'`: replace the first version line only
run "awk -v v='$VERSION' '!done && /^version *= *\"[^\"]+\"/ { sub(/\"[^\"]+\"/, \"\\\"\" v \"\\\"\"); done = 1 } { print }' '$WT/$PYPROJECT' > '$WT/$PYPROJECT.bumped' && mv '$WT/$PYPROJECT.bumped' '$WT/$PYPROJECT'"
run "git -C '$WT' add '$PYPROJECT'"
run "git -C '$WT' commit -q -m 'Release coach $TAG'"
run "git -C '$WT' tag -a '$TAG' -m 'Coach $TAG'"
run "git worktree remove --force '$WT'"

# --- 4. push, then publish the GitHub release ------------------------------------
# Before deploying: LIVE_DIR fetches from origin, so an unpushed ref cannot ship.
run "git push -q origin 'refs/heads/$REL_BRANCH:refs/heads/$REL_BRANCH'"
run "git push -q origin 'refs/tags/$TAG'"

# Tags sit on release-branch bump commits, not main, so prev-tag..ship-sha is exactly
# what main gained since the last release.
release_notes() {
  local range="$SHIP_SHA"
  git rev-parse -q --verify "refs/tags/v$PREV" >/dev/null 2>&1 && range="v$PREV..$SHIP_SHA"
  printf "## What's changed\n\n"
  git log --first-parent --format='- %s (%h)' "$range"
}
if ! command -v gh >/dev/null 2>&1; then
  say "gh not found — $TAG gets no release page; later: gh release create $TAG --generate-notes"
else
  NOTES_FILE="$(mktemp)"
  release_notes > "$NOTES_FILE"
  sed 's/^/    /' "$NOTES_FILE"
  if [ "$DRY_RUN" = 1 ]; then
    printf "  [dry-run] gh release create '%s' --title 'Coach %s' --notes-file <notes> --verify-tag\n" "$TAG" "$TAG"
  elif timeout 60 gh release create "$TAG" --title "Coach $TAG" --notes-file "$NOTES_FILE" --verify-tag; then
    say "release published for $TAG"
  else
    say "gh release create failed (non-fatal); later: gh release create $TAG --generate-notes"
  fi
  rm -f "$NOTES_FILE"
fi

# --- 5. deploy (fetch from origin, build, restart, health) ----------------------
say "deploying $TAG via $DEPLOY_SCRIPT"
if ! run "'$DEPLOY_SCRIPT' '$TAG'"; then
  notify "<b>ship-it $TAG FAILED</b>%0Adeploy-live.sh exited non-zero. Tag is on origin; rerun deploy-live.sh $TAG, never ship-it."
  die "deploy-live.sh failed — rerun '$DEPLOY_SCRIPT $TAG' (the tag is already on origin)"
fi

# --- 6. notify ------------------------------------------------------------------
notify "<b>ship-it $TAG COMPLETE</b>%0ACoach deployed from $REL_BRANCH (base ${SHIP_SHA:0:9})%0ATime: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
say "shipped $TAG"
