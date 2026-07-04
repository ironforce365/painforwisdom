#!/usr/bin/env bash
# Install (or update) the coach watchdog as a systemd --user service.
#
# The unit runs watchdog/runner.py from the LIVE checkout (painforwisdom-live),
# so a `git pull`/deploy there updates the watchdog on its next restart. This
# script copies the unit file, reloads systemd, and (re)starts the service.
set -euo pipefail

UNIT_NAME="painforwisdom-coach-watchdog.service"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UNIT_SRC="$SCRIPT_DIR/../systemd/$UNIT_NAME"
UNIT_DIR="$HOME/.config/systemd/user"

LIVE_DIR="${COACH_LIVE_DIR:-$HOME/workspace/painforwisdom-live}"
if [[ ! -f "$LIVE_DIR/services/coach/watchdog/runner.py" ]]; then
    echo "ERROR: $LIVE_DIR/services/coach/watchdog/runner.py not found." >&2
    echo "Deploy a coach tag containing the watchdog to the live dir first." >&2
    exit 1
fi
if [[ ! -f "$LIVE_DIR/services/coach/.env.coach" ]]; then
    echo "ERROR: $LIVE_DIR/services/coach/.env.coach not found (unit EnvironmentFile)." >&2
    exit 1
fi

mkdir -p "$UNIT_DIR"
cp "$UNIT_SRC" "$UNIT_DIR/$UNIT_NAME"
systemctl --user daemon-reload
systemctl --user enable "$UNIT_NAME"
systemctl --user restart "$UNIT_NAME"
sleep 2
systemctl --user status "$UNIT_NAME" --no-pager | head -8
echo
echo "Watchdog installed. Logs: journalctl --user -u $UNIT_NAME -f"
