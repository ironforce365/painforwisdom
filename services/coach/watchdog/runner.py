#!/usr/bin/env python3
"""Coach watchdog loop — HOST-side prober + healer + alerter.

Runs as a systemd --user service (see systemd/painforwisdom-coach-watchdog.service)
so it survives any container/stack failure — the exact blind spot of 2026-07-01→04,
when the in-stack "monitor" (a read-only conversation viewer) watched a 3-day DNS
outage without a peep. STDLIB ONLY: the host has no project venv; urllib +
subprocess is the whole dependency surface.

Every cycle (COACH_WATCHDOG_INTERVAL_S, default 60s):
  1. GET agent /health/deep on localhost:8800 — carries api_dns + mem0 checks run
     from INSIDE the container, where the outage lived.
  2. Check the telegram-bot container is running AND has produced log output
     recently — the bot polls Telegram every ~10s, so a quiet log means a hung
     process or the post-host-crash docker log black hole.
  3. Feed results into watchdog.logic.decide(); execute its actions:
     restart/recreate via `docker compose --env-file .env.coach`, alerts via the
     Telegram bot API (TELEGRAM_COACH_ALERT_CHAT_ID).

Self-heal target: failure → detected ≤60s → healed at the 2nd failing cycle
(~2min) → verified next cycle. Well under the 5-minute ceiling.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

# Allow running as a plain script (systemd ExecStart=…/watchdog/runner.py).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from watchdog.logic import Action, Probe, WatchdogState, decide  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("coach.watchdog")

AGENT_DEEP_URL = os.environ.get("COACH_AGENT_DEEP_URL", "http://localhost:8800/health/deep")
BOT_CONTAINER = os.environ.get("COACH_BOT_CONTAINER", "coach-telegram-bot-1")
# The bot logs a getUpdates line every ~10s; 5 minutes of silence is unambiguous.
BOT_LOG_STALE_S = int(os.environ.get("COACH_BOT_LOG_STALE_S", "300"))
INTERVAL_S = float(os.environ.get("COACH_WATCHDOG_INTERVAL_S", "60"))
COMPOSE_DIR = os.environ.get(
    "COACH_COMPOSE_DIR",
    str(Path.home() / "workspace/painforwisdom-live/services/coach"),
)


def _compose_cmd(*args: str) -> list[str]:
    return [
        "docker", "compose",
        "--env-file", str(Path(COMPOSE_DIR) / ".env.coach"),
        "-f", str(Path(COMPOSE_DIR) / "docker-compose.yml"),
        *args,
    ]


def probe_agent() -> tuple[bool, bool | None, bool | None]:
    """(agent_http, api_dns, mem0). 200 and 503 both carry a checks body; an
    unreachable/timeout agent yields (False, None, None) — unknown deep state."""
    try:
        req = urllib.request.Request(AGENT_DEEP_URL)
        with urllib.request.urlopen(req, timeout=20) as resp:  # noqa: S310 - localhost
            body = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        if e.code == 503:
            try:
                body = json.loads(e.read().decode())
            except Exception:  # noqa: BLE001
                return False, None, None
        else:
            return False, None, None
    except Exception:  # noqa: BLE001 - conn refused, timeout, bad json…
        return False, None, None
    checks = body.get("checks", {})
    return True, checks.get("api_dns"), checks.get("mem0")


def probe_bot() -> bool:
    """Bot container running AND its docker log has fresh output."""
    try:
        state = subprocess.run(
            ["docker", "inspect", BOT_CONTAINER, "--format", "{{.State.Running}}"],
            capture_output=True, text=True, timeout=15,
        )
        if state.returncode != 0 or state.stdout.strip() != "true":
            return False
        logs = subprocess.run(
            ["docker", "logs", BOT_CONTAINER, "--since", f"{BOT_LOG_STALE_S}s"],
            capture_output=True, text=True, timeout=15,
        )
        return bool(logs.stdout.strip() or logs.stderr.strip())
    except Exception:  # noqa: BLE001
        return False


def send_alert(message: str) -> None:
    """Best-effort Telegram DM to the admin chat. The watchdog runs on the host
    (working DNS even when containers are broken), so this path stays alive
    during exactly the outages it reports."""
    token = os.environ.get("TELEGRAM_COACH_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_COACH_ALERT_CHAT_ID", "")
    if not token or not chat_id:
        log.warning("alert not sent (no token/chat configured): %s", message)
        return
    try:
        data = urllib.parse.urlencode({"chat_id": chat_id, "text": f"🩺 {message}"}).encode()
        urllib.request.urlopen(  # noqa: S310 - fixed https host
            f"https://api.telegram.org/bot{token}/sendMessage", data=data, timeout=15
        )
    except Exception:  # noqa: BLE001 - alerting must never kill the loop
        log.exception("failed to send telegram alert")


def execute(action: Action) -> None:
    if action.kind == "alert":
        log.warning("ALERT: %s", action.message)
        send_alert(action.message)
        return
    if action.kind == "restart":
        cmd = _compose_cmd("restart", *action.services)
    elif action.kind == "recreate":
        cmd = _compose_cmd("up", "-d", "--force-recreate", "--no-deps", *action.services)
    else:
        log.error("unknown action kind %r", action.kind)
        return
    log.warning("healing: %s", " ".join(cmd))
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if out.returncode != 0:
            log.error("heal command failed rc=%d: %s", out.returncode, out.stderr[-500:])
            send_alert(f"coach watchdog: heal command FAILED (rc={out.returncode}) — {' '.join(action.services)}")
    except Exception:  # noqa: BLE001
        log.exception("heal command errored")


def run_once(state: WatchdogState) -> None:
    agent_http, api_dns, mem0 = probe_agent()
    bot_alive = probe_bot()
    probe = Probe(agent_http=agent_http, api_dns=api_dns, mem0=mem0, bot_alive=bot_alive)
    decision = decide(state, probe)
    if decision.actions:
        log.info("probe=%s -> %d action(s)", probe, len(decision.actions))
    for action in decision.actions:
        execute(action)


def main() -> None:
    log.info(
        "coach watchdog up: interval=%.0fs compose_dir=%s agent=%s",
        INTERVAL_S, COMPOSE_DIR, AGENT_DEEP_URL,
    )
    state = WatchdogState()
    while True:
        try:
            run_once(state)
        except Exception:  # noqa: BLE001 - the loop must survive anything
            log.exception("watchdog cycle failed")
        time.sleep(INTERVAL_S)


if __name__ == "__main__":
    main()
