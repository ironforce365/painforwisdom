#!/usr/bin/env bash
# Watchdog: alert if painforwisdom-daily-brief output is stale.
#
# Run from cron (NOT a systemd timer) so it survives the same failure modes
# that would silently kill the brief timer (manual `systemctl stop`, user
# manager death without linger, etc.).
#
# Detection logic:
#   1. Newest directory under briefs/<theme>/<date>--<slug>/ — if its mtime
#      is older than MAX_AGE_HOURS, the brief is stale.
#   2. As a secondary signal, query `systemctl --user is-active` on the
#      timer. Reports separately if user-systemd isn't reachable from cron.
#
# Dedupes alerts via a state file so a long outage doesn't spam every run.

set -uo pipefail

ROOT=/home/gonzalo/workspace/painforwisdom/painforwisdom
STATE_DIR="$ROOT/data"
STATE_FILE="$STATE_DIR/.daily_brief_watchdog.last_alert"
LOG_FILE="$STATE_DIR/daily_brief_watchdog.log"
MAX_AGE_HOURS=25
DEDUPE_HOURS=12
TIMER_UNIT=painforwisdom-daily-brief.timer

mkdir -p "$STATE_DIR"

ts() { date '+%Y-%m-%dT%H:%M:%S%z'; }
log() { printf '%s %s\n' "$(ts)" "$*" >> "$LOG_FILE"; }

now=$(date +%s)
problems=()

# --- 1) freshness of newest brief output -----------------------------------
newest_epoch=$(find "$ROOT/briefs" -mindepth 2 -maxdepth 2 -type d \
    -printf '%T@\n' 2>/dev/null | sort -nr | head -1 | cut -d. -f1)

if [ -z "${newest_epoch:-}" ]; then
    problems+=("no brief directories under $ROOT/briefs")
else
    age_s=$(( now - newest_epoch ))
    age_h=$(( age_s / 3600 ))
    if [ "$age_h" -ge "$MAX_AGE_HOURS" ]; then
        problems+=("last brief ${age_h}h ago (threshold ${MAX_AGE_HOURS}h)")
    fi
fi

# --- 2) timer liveness (best-effort; cron has no user-systemd env) ---------
export XDG_RUNTIME_DIR="/run/user/$(id -u)"
export DBUS_SESSION_BUS_ADDRESS="unix:path=$XDG_RUNTIME_DIR/bus"

heal_note=""

timer_state=$(systemctl --user is-active "$TIMER_UNIT" 2>/dev/null || true)
case "$timer_state" in
    active)
        : ;;
    "")
        problems+=("user systemd unreachable from cron (timer state unknown)") ;;
    *)
        # Heal-then-notify: try to restart the timer ourselves before paging.
        # If it comes back active, suppress the timer problem and surface the
        # heal action instead. If it stays down, fall through to the alert.
        log "AUTO_HEAL_ATTEMPT timer was state=$timer_state"
        systemctl --user reset-failed "$TIMER_UNIT" >> "$LOG_FILE" 2>&1 || true
        if systemctl --user start "$TIMER_UNIT" >> "$LOG_FILE" 2>&1; then
            sleep 2
            new_state=$(systemctl --user is-active "$TIMER_UNIT" 2>/dev/null || true)
            if [ "$new_state" = active ]; then
                log "AUTO_HEAL_OK timer recovered from $timer_state to $new_state"
                heal_note="auto-healed: timer was $timer_state, restarted ok"
            else
                problems+=("timer $TIMER_UNIT state=$timer_state; heal failed (now=$new_state)")
            fi
        else
            problems+=("timer $TIMER_UNIT state=$timer_state; heal failed (start rc=$?)")
        fi
        ;;
esac

# --- decide whether to alert ----------------------------------------------
if [ "${#problems[@]}" -eq 0 ]; then
    if [ -n "$heal_note" ]; then
        # Auto-heal happened with no other outstanding problem. Send a low-noise
        # notice (one-shot, no dedupe) so we know self-recovery fired. The
        # persistent timer will fire the missed run on its own.
        notice="ℹ️ daily-brief watchdog: $heal_note"
        if [ -f "$ROOT/.env" ]; then
            # shellcheck disable=SC1091
            set -a; . "$ROOT/.env"; set +a
        fi
        notice_chat_id="${TELEGRAM_DAILY_SUMMARY_CHAT_ID:-${TELEGRAM_CHAT_ID:-}}"
        TELEGRAM_CHAT_ID="$notice_chat_id" "$ROOT/telegram_io.sh" send "$notice" >> "$LOG_FILE" 2>&1 || true
        log "AUTO_HEAL_NOTICE_SENT $heal_note"
    else
        log "OK newest=${age_h}h timer=$timer_state"
    fi
    exit 0
fi

last_alert=0
[ -f "$STATE_FILE" ] && last_alert=$(cat "$STATE_FILE" 2>/dev/null || echo 0)
since_alert=$(( now - last_alert ))

if [ "$since_alert" -lt $(( DEDUPE_HOURS * 3600 )) ]; then
    log "SUPPRESSED (last alert ${since_alert}s ago) problems=${problems[*]}"
    exit 0
fi

msg="🚨 daily-brief watchdog: $(IFS='; '; echo "${problems[*]}")"
if [ -n "$heal_note" ]; then
    msg="$msg"$'\n'"note: $heal_note"
fi
msg="$msg"$'\n'"recover: systemctl --user reset-failed $TIMER_UNIT && systemctl --user start $TIMER_UNIT"

# Route alert to the daily-summary channel so it lands where Gonzalo reads the
# briefs — falls back to the pipeline default when the override is unset.
# Pulled from .env via a single source so cron (which has a bare env) sees it.
if [ -f "$ROOT/.env" ]; then
    # shellcheck disable=SC1091
    set -a; . "$ROOT/.env"; set +a
fi
alert_chat_id="${TELEGRAM_DAILY_SUMMARY_CHAT_ID:-${TELEGRAM_CHAT_ID:-}}"

if TELEGRAM_CHAT_ID="$alert_chat_id" "$ROOT/telegram_io.sh" send "$msg" >> "$LOG_FILE" 2>&1; then
    echo "$now" > "$STATE_FILE"
    log "ALERT_SENT problems=${problems[*]}"
else
    log "ALERT_SEND_FAILED problems=${problems[*]}"
    exit 1
fi
