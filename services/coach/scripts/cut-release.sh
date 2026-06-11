#!/usr/bin/env bash
# cut-release.sh <X.Y>
#
# Idempotently create release/X.Y.x from origin/main (or CUT_RELEASE_SOURCE
# override) and push to origin. Safe to re-run. Mirrors
# auto_heycrypto/scripts/cut-release.sh.
#
# Env overrides:
#   CUT_RELEASE_REMOTE  -- default: origin
#   CUT_RELEASE_SOURCE  -- default: ${CUT_RELEASE_REMOTE}/main (any ref: branch/tag/SHA)
#   CUT_RELEASE_REPO    -- default: ironforce365/painforwisdom (for the gh api hint)

set -euo pipefail

RED='\033[0;31m'; YELLOW='\033[1;33m'; GREEN='\033[0;32m'; NC='\033[0m'
info()  { echo -e "${GREEN}[cut-release]${NC} $*"; }
warn()  { echo -e "${YELLOW}[warn]${NC}         $*"; }
error() { echo -e "${RED}[error]${NC}        $*" >&2; }

MINOR="${1:-}"
if [[ -z "$MINOR" ]]; then
    error "Usage: $0 <X.Y>"
    error "Example: $0 0.1  -> creates release/0.1.x"
    exit 1
fi
if [[ ! "$MINOR" =~ ^[0-9]+\.[0-9]+$ ]]; then
    error "Invalid minor: '$MINOR'. Expected X.Y (e.g. 0.1)."
    exit 1
fi

REMOTE="${CUT_RELEASE_REMOTE:-origin}"
BRANCH="release/${MINOR}.x"
SOURCE="${CUT_RELEASE_SOURCE:-${REMOTE}/main}"
REPO="${CUT_RELEASE_REPO:-ironforce365/painforwisdom}"

if ! git rev-parse --git-dir >/dev/null 2>&1; then
    error "Not a git repository. cd into the repo and run again."
    exit 1
fi

info "Fetching $REMOTE..."
git fetch --tags --prune "$REMOTE"

if git ls-remote --exit-code --heads "$REMOTE" "refs/heads/$BRANCH" >/dev/null 2>&1; then
    info "Branch $BRANCH already exists on $REMOTE. Nothing to do."
    exit 0
fi

# Resolve source to a commit (direct ref, or refs/remotes/<source>).
if SOURCE_SHA="$(git rev-parse --verify "$SOURCE^{commit}" 2>/dev/null)"; then
    :
elif SOURCE_SHA="$(git rev-parse --verify "refs/remotes/$SOURCE^{commit}" 2>/dev/null)"; then
    :
else
    error "Source '$SOURCE' not found (tried direct ref and refs/remotes/$SOURCE)."
    exit 1
fi
SHORT_SHA="$(git rev-parse --short "$SOURCE_SHA")"

info "Creating $BRANCH from $SOURCE @ $SHORT_SHA"
git push "$REMOTE" "$SOURCE_SHA:refs/heads/$BRANCH"
info "Branch $BRANCH created and pushed to $REMOTE."

echo ""
echo "RECOMMENDED: apply GitHub branch protection. Run once:"
cat <<EOF

gh api -X PUT "repos/${REPO}/branches/release%2F${MINOR}.x/protection" \\
    -f required_status_checks=null \\
    -F enforce_admins=true \\
    -f required_pull_request_reviews[required_approving_review_count]=1 \\
    -f required_pull_request_reviews[dismiss_stale_reviews]=true \\
    -F allow_force_pushes=false \\
    -F allow_deletions=false \\
    -F restrictions=null

EOF
echo "Verify with:"
echo "  gh api repos/${REPO}/branches/release%2F${MINOR}.x/protection"
