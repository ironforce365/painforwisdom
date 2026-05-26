"""Quota monitor sums total_cost_usd across recent ResultMessage logs and alerts at 80%."""
from __future__ import annotations
from pathlib import Path
import json
from sidecar.quota_monitor import compute_burn, should_alert


def test_compute_burn_sums_logs(tmp_path: Path):
    log = tmp_path / "usage.jsonl"
    log.write_text(
        json.dumps({"total_cost_usd": 1.5}) + "\n" +
        json.dumps({"total_cost_usd": 2.75}) + "\n"
    )
    assert compute_burn(log) == 4.25


def test_alerts_above_threshold():
    assert should_alert(burn=80.0, cap=100.0, threshold=0.8) is True
    assert should_alert(burn=79.0, cap=100.0, threshold=0.8) is False
