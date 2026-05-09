#!/usr/bin/env bash
# Sandbox smoke test for the LangGraph content pipeline.
#
# Runs extract → kb_curator → writer/research → notion_blog/research →
# validator against a checked-in transcript fixture. No real video, no
# Whisper, no production Notion / vault writes (`--profile sandbox` swaps
# every side-effect target).
#
# Pre-flight (one-time, see .env.sandbox.template):
#   1. Duplicate the two prod Notion DBs into a sandbox workspace.
#   2. Create a parallel obsidian-vault-sandbox/ directory.
#   3. Fill in .env.sandbox with the IDs.
#
# Exit codes: 0 = PASS or PARTIAL, 2 = FAIL.

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_ROOT"

if [[ ! -f .env.sandbox ]]; then
    echo "[smoke] .env.sandbox missing — copy .env.sandbox.template, fill in" >&2
    echo "[smoke] sandbox Notion DB IDs and the sandbox vault path, then retry." >&2
    exit 1
fi

PYTHON_BIN="${PYTHON_BIN:-/home/gonzalo/miniconda3/envs/painforwisdom-poc/bin/python}"
FIXTURE="${SMOKE_FIXTURE:-tests/fixtures/transcript_2026-04-14.txt}"

if [[ ! -f "$FIXTURE" ]]; then
    echo "[smoke] fixture not found: $FIXTURE" >&2
    exit 1
fi

echo "[smoke] profile=sandbox fixture=$FIXTURE python=$PYTHON_BIN"
exec "$PYTHON_BIN" -m pipeline.run \
    --profile sandbox \
    --from-transcript "$FIXTURE" \
    --auto-approve \
    --telegram-on-error
