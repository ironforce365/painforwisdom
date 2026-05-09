#!/usr/bin/env bash
# Reset the smoke-test sandbox to a clean state.
#
#   1. Discard uncommitted changes in obsidian-vault-sandbox/ worktree
#      (entries, themes, frameworks, _index, book-outline written by the
#      previous run).
#   2. Archive every page currently living in the sandbox Notion DBs
#      (Blog + Research). Archived = soft-deleted; pages disappear from the
#      DB UI but remain recoverable from Notion's trash for 30 days.
#
# Required env (loaded from .env.sandbox by default):
#   NOTION_API_KEY
#   NOTION_BLOG_DATA_SOURCE_ID
#   NOTION_RESEARCH_DATA_SOURCE_ID
#
# Exit codes: 0 = reset OK, non-zero = something refused to clean up.

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_ROOT"

if [[ ! -f .env.sandbox ]]; then
    echo "[reset] .env.sandbox missing — abort." >&2
    exit 1
fi

# Load .env.sandbox without sourcing (values may contain spaces).
NOTION_API_KEY=$(grep -E '^NOTION_API_KEY=' .env.sandbox | cut -d= -f2-)
BLOG_DS=$(grep -E '^NOTION_BLOG_DATA_SOURCE_ID=' .env.sandbox | cut -d= -f2-)
RESEARCH_DS=$(grep -E '^NOTION_RESEARCH_DATA_SOURCE_ID=' .env.sandbox | cut -d= -f2-)
# VAULT_PATH override from .env.sandbox — falls back to the local
# obsidian-vault-sandbox/ worktree when unset (typical for laptop runs).
VAULT_FROM_ENV=$(grep -E '^VAULT_PATH=' .env.sandbox | cut -d= -f2-)

if [[ -z "$NOTION_API_KEY" || -z "$BLOG_DS" || -z "$RESEARCH_DS" ]]; then
    echo "[reset] missing NOTION_API_KEY / DATA_SOURCE_ID env vars" >&2
    exit 1
fi

VAULT_SANDBOX="${VAULT_FROM_ENV:-$PROJECT_ROOT/obsidian-vault-sandbox}"
PYTHON_BIN="${PYTHON_BIN:-/home/gonzalo/miniconda3/envs/painforwisdom-poc/bin/python}"

# ---- 1. Vault worktree --------------------------------------------------
if [[ -d "$VAULT_SANDBOX/.git" || -f "$VAULT_SANDBOX/.git" ]]; then
    echo "[reset] reverting vault worktree: $VAULT_SANDBOX"
    git -C "$VAULT_SANDBOX" checkout -- . 2>/dev/null || true
    git -C "$VAULT_SANDBOX" clean -fd
    DIRTY=$(git -C "$VAULT_SANDBOX" status --porcelain | wc -l)
    if [[ "$DIRTY" -ne 0 ]]; then
        echo "[reset] vault still dirty after reset:" >&2
        git -C "$VAULT_SANDBOX" status --short >&2
        exit 2
    fi
else
    echo "[reset] $VAULT_SANDBOX not a worktree — skipping" >&2
fi

# ---- 2. Notion DB archive ----------------------------------------------
# Notion data sources are queried via /v1/data_sources/<ds_id>/query (modern
# API, version 2025-09-03). We page through, then PATCH each page with
# archived=true.
archive_data_source () {
    local label="$1"
    local ds_id="$2"
    echo "[reset] archiving pages in $label ($ds_id)"
    NOTION_API_KEY="$NOTION_API_KEY" NOTION_DS_ID="$ds_id" \
        "$PYTHON_BIN" - <<'PYEOF'
import json
import os
import sys
import time
import urllib.request

API_KEY = os.environ["NOTION_API_KEY"]
DS_ID = os.environ["NOTION_DS_ID"]
HEADERS = {
    "Authorization": f"Bearer {API_KEY}",
    "Notion-Version": "2025-09-03",
    "Content-Type": "application/json",
}

def request(method, url, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers=HEADERS)
    with urllib.request.urlopen(req) as r:
        return json.load(r)

def query_pages():
    pages, cursor = [], None
    while True:
        body = {"page_size": 100}
        if cursor:
            body["start_cursor"] = cursor
        resp = request(
            "POST",
            f"https://api.notion.com/v1/data_sources/{DS_ID}/query",
            body,
        )
        pages.extend(resp.get("results", []))
        if not resp.get("has_more"):
            break
        cursor = resp.get("next_cursor")
    return pages

def archive(page_id):
    request(
        "PATCH",
        f"https://api.notion.com/v1/pages/{page_id}",
        {"archived": True},
    )

pages = query_pages()
live = [p for p in pages if not p.get("archived")]
print(f"[reset]   {len(live)} live pages to archive (of {len(pages)} total)")
for p in live:
    archive(p["id"])
    time.sleep(0.4)  # respect ~3 req/s rate limit
print(f"[reset]   archived {len(live)} pages")
PYEOF
}

archive_data_source "Blog" "$BLOG_DS"
archive_data_source "Research" "$RESEARCH_DS"

echo "[reset] done — sandbox clean"
