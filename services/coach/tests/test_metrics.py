"""TurnMetrics: per-turn latency percentiles + timeout/down alerting.

p95 monitoring for the bot→agent coaching turn. Today there is zero visibility
into turn latency or failure outcomes; these tests pin the percentile math
(nearest-rank), outcome counting, and the alert threshold behaviour so latency
regressions and timeouts are caught before a user reports them.
"""
from __future__ import annotations

import logging

import pytest

from telegram_bot.metrics import (
    DEFAULT_P95_ALERT_THRESHOLD,
    TurnMetrics,
    record,
    recorder,
)


def test_empty_snapshot_is_graceful():
    m = TurnMetrics()
    snap = m.snapshot()
    assert snap["count"] == 0
    assert snap["p50"] is None
    assert snap["p95"] is None
    assert snap["p99"] is None
    assert snap["outcomes"] == {"ok": 0, "timeout": 0, "down": 0}


def test_single_sample_all_percentiles_equal():
    m = TurnMetrics()
    m.record(12.5, "ok")
    snap = m.snapshot()
    assert snap["count"] == 1
    assert snap["p50"] == 12.5
    assert snap["p95"] == 12.5
    assert snap["p99"] == 12.5


def test_percentiles_nearest_rank_on_known_sample():
    # Nearest-rank: rank = ceil(p/100 * N), value at that 1-based rank.
    # Sample 1..100 (N=100): p50→rank 50→50, p95→rank 95→95, p99→rank 99→99.
    m = TurnMetrics()
    for v in range(1, 101):
        m.record(float(v), "ok")
    snap = m.snapshot()
    assert snap["count"] == 100
    assert snap["p50"] == 50.0
    assert snap["p95"] == 95.0
    assert snap["p99"] == 99.0


def test_percentiles_unsorted_input():
    # Recording order must not affect the result.
    m = TurnMetrics()
    for v in [100, 1, 50, 95, 99, 2, 75]:
        m.record(float(v), "ok")
    snap = m.snapshot()
    # N=7 sorted: [1,2,50,75,95,99,100]
    # p50 → ceil(.5*7)=4 → 75 ; p95 → ceil(.95*7)=7 → 100 ; p99 → 7 → 100
    assert snap["p50"] == 75.0
    assert snap["p95"] == 100.0
    assert snap["p99"] == 100.0


def test_outcome_counting():
    m = TurnMetrics()
    m.record(1.0, "ok")
    m.record(2.0, "ok")
    m.record(3.0, "timeout")
    m.record(4.0, "down")
    snap = m.snapshot()
    assert snap["count"] == 4
    assert snap["outcomes"] == {"ok": 2, "timeout": 1, "down": 1}


def test_invalid_outcome_rejected():
    m = TurnMetrics()
    with pytest.raises(ValueError):
        m.record(1.0, "weird")


def test_alert_silent_below_threshold():
    m = TurnMetrics(p95_threshold=150.0)
    for v in range(1, 101):  # p95 = 95s, all ok
        m.record(float(v), "ok")
    assert m.check_alert() is False


def test_alert_fires_above_threshold(caplog):
    m = TurnMetrics(p95_threshold=150.0)
    for _ in range(100):  # p95 = 200s > 150s, all ok
        m.record(200.0, "ok")
    with caplog.at_level(logging.WARNING):
        assert m.check_alert() is True
    assert any(r.levelno == logging.WARNING for r in caplog.records)


def test_alert_fires_on_timeout_outcome(caplog):
    m = TurnMetrics(p95_threshold=150.0)
    m.record(1.0, "ok")
    m.record(2.0, "timeout")  # fast but a timeout occurred in the window
    with caplog.at_level(logging.WARNING):
        assert m.check_alert() is True
    assert any(r.levelno == logging.WARNING for r in caplog.records)


def test_alert_fires_on_down_outcome(caplog):
    m = TurnMetrics(p95_threshold=150.0)
    m.record(1.0, "down")
    with caplog.at_level(logging.WARNING):
        assert m.check_alert() is True


def test_default_threshold_is_near_client_read_ceiling():
    # Default sits just under the 180s client read ceiling.
    assert DEFAULT_P95_ALERT_THRESHOLD == 150.0


def test_module_singleton_record(monkeypatch):
    # The module-level record() must feed the shared recorder singleton.
    monkeypatch.setattr(recorder, "_latencies", [])
    monkeypatch.setattr(recorder, "_outcomes", {"ok": 0, "timeout": 0, "down": 0})
    record(5.0, "ok")
    snap = recorder.snapshot()
    assert snap["count"] == 1
    assert snap["outcomes"]["ok"] == 1
