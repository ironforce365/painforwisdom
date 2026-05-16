#!/bin/bash
# telegram_io.sh — send messages and wait for replies via Telegram
#
# Usage:
#   ./telegram_io.sh send "Your message here"
#   ./telegram_io.sh wait_reply [timeout_seconds]   # default: 3600 (1 hour)
#   ./telegram_io.sh ask "Your question here" [timeout_seconds]  # send + wait_reply combined
#
# Requires: TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env or environment

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Load .env if present
if [ -f "$SCRIPT_DIR/.env" ]; then
    # shellcheck disable=SC1091
    set -a
    source "$SCRIPT_DIR/.env"
    set +a
fi

BOT_TOKEN="${TELEGRAM_BOT_TOKEN:-}"
CHAT_ID="${TELEGRAM_CHAT_ID:-}"
POLL_INTERVAL=5   # seconds between polls

if [ -z "$BOT_TOKEN" ] || [ -z "$CHAT_ID" ]; then
    echo "ERROR: TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set in .env or environment" >&2
    exit 1
fi

_send() {
    # Interpret \n as real newlines so multi-line messages render correctly.
    # $TELEGRAM_PARSE_MODE (optional) selects Telegram's parse_mode for the
    # message — "HTML" enables <a href="...">link</a>, <b>, <i>, <code>, etc.
    # When unset, plain text is sent (current behavior).
    local text response ok parse_mode_args=()
    text=$(printf '%b' "$1")
    if [ -n "${TELEGRAM_PARSE_MODE:-}" ]; then
        parse_mode_args=(--data-urlencode "parse_mode=${TELEGRAM_PARSE_MODE}")
    fi
    response=$(curl -s -X POST "https://api.telegram.org/bot${BOT_TOKEN}/sendMessage" \
        --data-urlencode "chat_id=${CHAT_ID}" \
        "${parse_mode_args[@]}" \
        --data-urlencode "text=${text}")
    ok=$(echo "$response" | python3 -c "import sys,json; print(json.load(sys.stdin).get('ok','false'))" 2>/dev/null)
    if [ "$ok" != "True" ]; then
        echo "WARNING: Telegram send failed: $response" >&2
    fi
}

_get_last_update_id() {
    curl -s "https://api.telegram.org/bot${BOT_TOKEN}/getUpdates?offset=-1&limit=1" | \
        python3 -c "
import sys, json
data = json.load(sys.stdin)
updates = data.get('result', [])
print(updates[-1]['update_id'] if updates else 0)
" 2>/dev/null || echo 0
}

_wait_reply() {
    # Anchor to the current last update so we only catch NEW messages
    local last_id
    last_id=$(_get_last_update_id)
    local offset=$((last_id + 1))

    while true; do

        local response
        response=$(curl -s "https://api.telegram.org/bot${BOT_TOKEN}/getUpdates?offset=${offset}&timeout=30")

        local reply
        reply=$(echo "$response" | python3 -c "
import sys, json
data = json.load(sys.stdin)
updates = data.get('result', [])
for u in updates:
    msg = u.get('message', {})
    text = msg.get('text', '').strip()
    if text:
        print(text)
        break
" 2>/dev/null)

        if [ -n "$reply" ]; then
            echo "$reply"
            return 0
        fi

        sleep "$POLL_INTERVAL"
    done
}

case "${1:-}" in
    send)
        _send "${2:?Usage: $0 send <message>}"
        ;;
    wait_reply)
        _wait_reply
        ;;
    ask)
        # Send a question and wait indefinitely for a reply
        _send "${2:?Usage: $0 ask <message>}"
        _wait_reply
        ;;
    *)
        echo "Usage: $0 send <message> | wait_reply [timeout] | ask <message> [timeout]" >&2
        exit 1
        ;;
esac
