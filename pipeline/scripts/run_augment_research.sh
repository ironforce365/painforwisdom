#!/usr/bin/env bash
# Wrapper invoked by painforwisdom-augment-research.service.
#
# Workflow:
#  1. Refresh the audit JSONL against current Notion state.
#  2. Run the augmenter over the fresh JSONL (z-library bridge first for
#     Type=Book rows, LLM web-search analog fallback otherwise).
#
# Idempotent: rows already classified "reachable" by the audit are skipped
# by the augmenter, so re-runs only touch new/failed rows.

set -euo pipefail

# Self-locating: root at THIS script's own checkout (dev, or the tag-pinned
# painforwisdom-live), so the systemd unit picks the environment purely by
# which copy of the script it invokes. A hardcoded dev path here silently
# defeated the dev/live split — the live unit would cd back into dev and run
# stale code (2026-06-08).
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PYTHON="/home/gonzalo/miniconda3/envs/painforwisdom-poc/bin/python"
# Cost cap disabled: user runs on Anthropic Ultra/Max subscription, no $ gating.
# Script requires the flag, so pass a number larger than any plausible run.
MAX_COST_USD=999999

cd "$PROJECT_ROOT"

TODAY="$(date +%F)"
AUDIT_JSONL="$PROJECT_ROOT/reports/research-audit-${TODAY}.jsonl"

echo "[augment-timer] $(date -Iseconds) start"
echo "[augment-timer] refreshing audit -> $AUDIT_JSONL"
"$PYTHON" -m pipeline.scripts.audit_research_tasks

if [[ ! -s "$AUDIT_JSONL" ]]; then
  echo "[augment-timer] ERROR: audit produced no JSONL at $AUDIT_JSONL" >&2
  exit 2
fi

echo "[augment-timer] running augmenter (max-cost-usd=$MAX_COST_USD)"
"$PYTHON" -m pipeline.scripts.augment_research_tasks \
  --audit-jsonl "$AUDIT_JSONL" \
  --max-cost-usd "$MAX_COST_USD" \
  --apply

echo "[augment-timer] $(date -Iseconds) done"
