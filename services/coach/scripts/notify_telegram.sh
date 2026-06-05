#!/usr/bin/env bash
# notify_telegram.sh "<message>"
#
# Send a Telegram message via the coach bot using TELEGRAM_COACH_BOT_TOKEN +
# TELEGRAM_COACH_ALERT_CHAT_ID from the env (loaded from .env.coach if present).
# Silent skip when either is missing -- never fails the calling script.
# Mirrors auto_heycrypto/scripts/notify_telegram.sh.
#
# COACH_NOTIFY_DRY_RUN=1 prints the resolved message and exits 0 without calling
# the Telegram API (used by tests so they never hit api.telegram.org).

set -euo pipefail

MESSAGE="${1:-}"
if [[ -z "$MESSAGE" ]]; then
    echo "[telegram] usage: $0 <message>" >&2
    exit 0
fi

# Tag outbound messages so they stand out in a shared alert channel. Skip when
# the body already says "coach" (case-insensitive) to avoid a double prefix.
PROJECT_TAG="[coach]"
_lower="${MESSAGE,,}"
if [[ "$_lower" != *"coach"* ]]; then
    MESSAGE="${PROJECT_TAG} ${MESSAGE}"
fi

if [[ "${COACH_NOTIFY_DRY_RUN:-0}" == "1" ]]; then
    echo "[telegram/dry-run] would send: ${MESSAGE}"
    exit 0
fi

# Load .env.coach so this works whether called from ship-it.sh (cwd = dev repo)
# or from the live checkout. COACH_NOTIFY_ENV overrides the search.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_CANDIDATES=(
    "${COACH_NOTIFY_ENV:-}"
    "$SCRIPT_DIR/../.env.coach"
    "$HOME/workspace/painforwisdom-live/services/coach/.env.coach"
)
for _env in "${ENV_CANDIDATES[@]}"; do
    if [[ -n "$_env" && -f "$_env" ]]; then
        # shellcheck disable=SC1090
        set -a; . "$_env"; set +a
        break
    fi
done

if [[ -z "${TELEGRAM_COACH_BOT_TOKEN:-}" || -z "${TELEGRAM_COACH_ALERT_CHAT_ID:-}" ]]; then
    echo "[telegram] TELEGRAM_COACH_BOT_TOKEN / TELEGRAM_COACH_ALERT_CHAT_ID not configured; skipping." >&2
    exit 0
fi

API_URL="https://api.telegram.org/bot${TELEGRAM_COACH_BOT_TOKEN}/sendMessage"

if ! curl -sS --fail --max-time 10 \
    -X POST "$API_URL" \
    --data-urlencode "chat_id=${TELEGRAM_COACH_ALERT_CHAT_ID}" \
    --data-urlencode "text=${MESSAGE}" \
    --data-urlencode "parse_mode=HTML" >/dev/null
then
    echo "[telegram] WARNING: failed to send notification (continuing)." >&2
fi

exit 0
