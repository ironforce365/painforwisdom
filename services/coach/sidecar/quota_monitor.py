"""Sum total_cost_usd from agent service usage log; alert Telegram at threshold.

Note: total_cost_usd is the SDK's client-side estimate, not authoritative billing."""
from __future__ import annotations
from pathlib import Path
import json
import os
import logging

log = logging.getLogger("coach.quota")


def compute_burn(usage_log: Path) -> float:
    total = 0.0
    for line in Path(usage_log).read_text().splitlines():
        try:
            total += float(json.loads(line).get("total_cost_usd", 0.0))
        except Exception:
            continue
    return total


def should_alert(*, burn: float, cap: float, threshold: float) -> bool:
    return burn / max(cap, 1e-9) >= threshold


def alert_telegram(message: str) -> None:
    import httpx
    token = os.environ["TELEGRAM_COACH_BOT_TOKEN"]
    chat_id = os.environ["TELEGRAM_COACH_ALERT_CHAT_ID"]
    httpx.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": message}, timeout=10,
    )


def main() -> int:
    usage_log = Path(os.environ.get("COACH_USAGE_LOG", "/data/usage.jsonl"))
    cap = float(os.environ.get("COACH_QUOTA_CAP_USD", "100"))
    threshold = float(os.environ.get("COACH_QUOTA_ALERT_THRESHOLD", "0.8"))
    burn = compute_burn(usage_log)
    log.info("burn=%.2f cap=%.2f", burn, cap)
    if should_alert(burn=burn, cap=cap, threshold=threshold):
        alert_telegram(f"Coach quota burn at {burn:.2f} / {cap:.2f} USD ({burn/cap:.0%}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
