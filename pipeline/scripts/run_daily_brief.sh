#!/usr/bin/env bash
# Wrapper invoked by painforwisdom-daily-brief.service.
#
# Purpose: pull the latest merged main before running the daily summarizer,
# so freshly-merged PRs take effect on the next scheduled run without manual
# `git pull`. The 2026-05-21 incident — local `main` had diverged from
# `origin/main` for 7 days, so the 06:00 run executed pre-`eea120b` code and
# silently dropped the slides/mindmap/infographic/vault feature — motivated
# this wrapper.
#
# Behavior:
#   1. `git fetch origin main`.
#   2. `git pull --ff-only origin main` — refuses non-FF merges so any local
#      ad-hoc commits or unpushed work block the run instead of being silently
#      rebased over. The watchdog will notify on the failure.
#   3. Invoke `python -m pipeline.summarize_daily "$@"` with the unit's args.
#
# Local-only commits (e.g. docs branches) MUST be pushed via PR or rebased
# manually; this script does not rewrite history.

set -euo pipefail

PROJECT_ROOT="/home/gonzalo/workspace/painforwisdom/painforwisdom"
PYTHON="/home/gonzalo/miniconda3/envs/painforwisdom-poc/bin/python"

cd "$PROJECT_ROOT"

echo "[daily-brief] $(date -Iseconds) start"

# Step 1: fetch — surfacing pulls without merging
if ! git fetch --quiet origin main; then
  echo "[daily-brief] WARNING: git fetch failed — running on stale main" >&2
fi

# Step 2: fast-forward only. If local main is ahead of origin/main (local
# commits not pushed) this exits non-zero; the wrapper continues so the run
# itself doesn't die just because of a dirty branch. The user/watchdog gets a
# loud log message either way.
if git pull --ff-only --quiet origin main; then
  echo "[daily-brief] main fast-forwarded to $(git rev-parse --short HEAD)"
else
  echo "[daily-brief] WARNING: cannot fast-forward main — local has un-pushed commits or diverged. Running on current HEAD: $(git rev-parse --short HEAD)" >&2
fi

# Step 3: run the summarizer with the unit's args forwarded verbatim
echo "[daily-brief] invoking pipeline.summarize_daily $*"
"$PYTHON" -m pipeline.summarize_daily "$@"

echo "[daily-brief] $(date -Iseconds) done"
