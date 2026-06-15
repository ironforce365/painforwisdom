"""Unit tests for shared.perf — the per-step turn timing helper.

Host-safe (stdlib only): no fastapi / claude_agent_sdk needed. We attach a
capture handler directly to the `coach.perf` logger so the assertions hold
regardless of root-logger / propagation config.
"""
import logging

import pytest

from shared.perf import perf_step


def _capture():
    """Attach a record-capturing handler to coach.perf; returns (records, detach)."""
    records: list[logging.LogRecord] = []
    handler = logging.Handler()
    handler.emit = records.append  # type: ignore[method-assign]
    log = logging.getLogger("coach.perf")
    log.addHandler(handler)
    return records, lambda: log.removeHandler(handler)


def test_perf_step_logs_step_and_ms_and_fields():
    records, detach = _capture()
    try:
        with perf_step("gate_judge", gate=True):
            pass
    finally:
        detach()
    msgs = [r.getMessage() for r in records]
    assert any("step=gate_judge" in m and "ms=" in m and "gate=True" in m for m in msgs)


def test_perf_step_emits_timing_then_reraises_on_error():
    records, detach = _capture()
    try:
        with pytest.raises(ValueError):
            with perf_step("boom"):
                raise ValueError("kaboom")
    finally:
        detach()
    msgs = [r.getMessage() for r in records]
    # The timing line is still emitted, and tagged with the exception type.
    assert any("step=boom" in m and "ms=" in m and "error=ValueError" in m for m in msgs)


def test_perf_step_logging_failure_never_breaks_body(monkeypatch):
    # Even if the logger itself raises, the wrapped body's result/exception flow
    # must be unaffected (logging is best-effort).
    import shared.perf as perf

    def _boom(*a, **k):
        raise RuntimeError("logger down")

    monkeypatch.setattr(perf._LOG, "info", _boom)
    ran = []
    with perf_step("safe"):
        ran.append(1)
    assert ran == [1]
