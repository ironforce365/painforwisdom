#!/usr/bin/env bash
# Install (or update) the pipeline systemd --user units: console, daily-brief,
# augment-research.
#
# Units run code from the tag-pinned LIVE checkout (painforwisdom-live, moved by
# services/coach/scripts/deploy-live.sh) and read/write state under the dev
# folder via PAINFORWISDOM_STATE_ROOT. Enables and restarts the console. Timers
# are copied only, never enabled (`systemctl --user enable --now <name>.timer`).
#
#   Usage: install-pipeline-units.sh [--dry-run] [--unit-dir <dir>] [--no-restart]
#     --dry-run     print the plan and the rendered console unit, touch nothing
#     --unit-dir    write units to <dir> and skip systemctl: systemd never reads it
#     --no-restart  enable the console without starting it; the caller restarts
#
#   Env: PFW_LIVE_DIR, PFW_STATE_ROOT, PFW_PYTHON override the paths.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UNIT_SRC_DIR="$SCRIPT_DIR/../systemd"
UNIT_DIR="$HOME/.config/systemd/user"
LIVE_DIR="${PFW_LIVE_DIR:-$HOME/workspace/painforwisdom-live}"
STATE_ROOT="${PFW_STATE_ROOT:-$HOME/workspace/painforwisdom/painforwisdom}"
PYTHON="${PFW_PYTHON:-$HOME/miniconda3/envs/painforwisdom-poc/bin/python}"
CONSOLE_UNIT=painforwisdom-console.service
UNITS=(
    painforwisdom-daily-brief.service painforwisdom-daily-brief.timer
    painforwisdom-augment-research.service painforwisdom-augment-research.timer
)

DRY_RUN=0
NO_RESTART=0
USE_SYSTEMCTL=1
while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run) DRY_RUN=1; shift ;;
        --no-restart) NO_RESTART=1; shift ;;
        --unit-dir) UNIT_DIR="${2:?--unit-dir needs a directory}"; USE_SYSTEMCTL=0; shift 2 ;;
        *) echo "ERROR: unknown argument: $1" >&2; exit 2 ;;
    esac
done

# A live tag older than the state-root change would read state from LIVE_DIR
# (no .env, empty books/), so refuse until a tag carrying it is deployed.
if ! grep -q PAINFORWISDOM_STATE_ROOT "$LIVE_DIR/pipeline/runtime.py" 2>/dev/null; then
    echo "ERROR: $LIVE_DIR/pipeline/runtime.py lacks PAINFORWISDOM_STATE_ROOT." >&2
    echo "Ship a tag containing it first (services/coach/scripts/ship-it.sh)." >&2
    exit 1
fi
if [[ ! -f "$STATE_ROOT/.env" ]]; then
    echo "ERROR: $STATE_ROOT/.env not found (pipeline credentials)." >&2
    exit 1
fi

render_console() {
    sed -e "s|@LIVE_DIR@|$LIVE_DIR|g" \
        -e "s|@STATE_ROOT@|$STATE_ROOT|g" \
        -e "s|@PYTHON_DIR@|$(dirname "$PYTHON")|g" \
        -e "s|@PYTHON@|$PYTHON|g" \
        "$UNIT_SRC_DIR/$CONSOLE_UNIT"
}

if [[ "$DRY_RUN" == 1 ]]; then
    echo "Would chmod 600 $STATE_ROOT/.env"
    echo "Would install to $UNIT_DIR: $CONSOLE_UNIT ${UNITS[*]}"
    if [[ "$USE_SYSTEMCTL" == 0 ]]; then
        echo "Would skip systemctl (--unit-dir)"
    elif [[ "$NO_RESTART" == 1 ]]; then
        echo "Would daemon-reload, enable $CONSOLE_UNIT (caller restarts)"
    else
        echo "Would daemon-reload, enable --now and restart $CONSOLE_UNIT"
    fi
    echo "--- $CONSOLE_UNIT"
    render_console
    exit 0
fi

chmod 600 "$STATE_ROOT/.env"

mkdir -p "$UNIT_DIR"
for u in "${UNITS[@]}"; do
    cp "$UNIT_SRC_DIR/$u" "$UNIT_DIR/$u"
done
render_console > "$UNIT_DIR/$CONSOLE_UNIT"

if [[ "$USE_SYSTEMCTL" == 0 ]]; then
    echo "Units written to $UNIT_DIR; systemctl skipped."
    exit 0
fi

systemctl --user daemon-reload
if [[ "$NO_RESTART" == 1 ]]; then
    systemctl --user enable "$CONSOLE_UNIT"
else
    systemctl --user enable --now "$CONSOLE_UNIT"
    # enable --now leaves an already-active unit on its old code.
    systemctl --user restart "$CONSOLE_UNIT"
fi
systemctl --user list-timers --all | grep painforwisdom || true
echo
echo "Units installed. Verify: systemctl --user cat $CONSOLE_UNIT"
