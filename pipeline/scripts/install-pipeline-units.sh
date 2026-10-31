#!/usr/bin/env bash
# Install (or update) the daily-brief + augment-research systemd --user units.
#
# Units run code from the tag-pinned LIVE checkout (painforwisdom-live, moved by
# services/coach/scripts/deploy-live.sh) and read/write state under the dev
# folder via PAINFORWISDOM_STATE_ROOT. Copies units and daemon-reloads; does NOT
# enable or start timers (`systemctl --user enable --now <name>.timer` for that).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UNIT_SRC_DIR="$SCRIPT_DIR/../systemd"
UNIT_DIR="$HOME/.config/systemd/user"
LIVE_DIR="$HOME/workspace/painforwisdom-live"
STATE_ROOT="$HOME/workspace/painforwisdom/painforwisdom"
UNITS=(
    painforwisdom-daily-brief.service painforwisdom-daily-brief.timer
    painforwisdom-augment-research.service painforwisdom-augment-research.timer
)

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
chmod 600 "$STATE_ROOT/.env"

mkdir -p "$UNIT_DIR"
for u in "${UNITS[@]}"; do
    cp "$UNIT_SRC_DIR/$u" "$UNIT_DIR/$u"
done
systemctl --user daemon-reload
systemctl --user list-timers --all | grep painforwisdom || true
echo
echo "Units installed. Verify: systemctl --user cat painforwisdom-daily-brief.service"
