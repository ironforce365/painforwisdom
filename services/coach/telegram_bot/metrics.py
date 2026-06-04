"""Per-turn observability for the bot→agent coaching turn.

Today there is zero visibility into how long a coaching turn takes or how often
it times out / falls over. This module records each turn's latency and outcome,
exposes p50/p95/p99 (pure-python, no numpy — torch/numpy is heavy in this image)
and fires a WARNING-level alert when p95 latency creeps toward the 180s client
read ceiling or when any timeout/down outcome lands in the window.

Callers use the module-level ``record(latency, outcome)`` helper and never have
to manage instances; the shared :data:`recorder` singleton holds the window.
"""
from __future__ import annotations

import logging
import math
from typing import Literal, Optional

log = logging.getLogger(__name__)

Outcome = Literal["ok", "timeout", "down"]
_OUTCOMES: tuple[Outcome, ...] = ("ok", "timeout", "down")

# Default p95 alert ceiling: just under the 180s client *read* timeout, so a
# regression is flagged before turns actually start crossing the wire deadline.
DEFAULT_P95_ALERT_THRESHOLD = 150.0


def _percentile(sorted_values: list[float], p: float) -> float:
    """Nearest-rank percentile of an already-sorted, non-empty list.

    rank = ceil(p/100 * N), clamped to [1, N], value taken at that 1-based rank.
    Nearest-rank (rather than linear interpolation) is chosen so reported
    percentiles are always real observed latencies, which is what we want when
    reasoning about "did a real turn cross the ceiling".
    """
    n = len(sorted_values)
    rank = math.ceil(p / 100.0 * n)
    rank = max(1, min(rank, n))
    return sorted_values[rank - 1]


class TurnMetrics:
    """Sliding-window-free accumulator of turn latencies and outcomes."""

    def __init__(self, p95_threshold: float = DEFAULT_P95_ALERT_THRESHOLD):
        self.p95_threshold = p95_threshold
        self._latencies: list[float] = []
        self._outcomes: dict[str, int] = {o: 0 for o in _OUTCOMES}

    def record(self, latency_seconds: float, outcome: Outcome) -> None:
        if outcome not in self._outcomes:
            raise ValueError(
                f"unknown outcome {outcome!r}; expected one of {_OUTCOMES}"
            )
        self._latencies.append(float(latency_seconds))
        self._outcomes[outcome] += 1

    def snapshot(self) -> dict:
        """Return total count, p50/p95/p99 latency, and per-outcome counts.

        Percentiles are ``None`` when no samples have been recorded.
        """
        if self._latencies:
            ordered = sorted(self._latencies)
            p50: Optional[float] = _percentile(ordered, 50)
            p95: Optional[float] = _percentile(ordered, 95)
            p99: Optional[float] = _percentile(ordered, 99)
        else:
            p50 = p95 = p99 = None
        return {
            "count": len(self._latencies),
            "p50": p50,
            "p95": p95,
            "p99": p99,
            "outcomes": dict(self._outcomes),
        }

    def check_alert(self) -> bool:
        """Return True (and log a WARNING) if the window is unhealthy.

        Unhealthy means p95 latency exceeds ``p95_threshold`` OR any timeout/down
        outcome has been recorded. Silent and False otherwise.
        """
        snap = self.snapshot()
        failures = snap["outcomes"]["timeout"] + snap["outcomes"]["down"]
        p95 = snap["p95"]
        p95_breach = p95 is not None and p95 > self.p95_threshold

        if p95_breach or failures:
            log.warning(
                "coach turn health alert: p95=%.1fs (threshold=%.1fs) "
                "timeouts=%d down=%d over %d turns",
                p95 if p95 is not None else float("nan"),
                self.p95_threshold,
                snap["outcomes"]["timeout"],
                snap["outcomes"]["down"],
                snap["count"],
            )
            return True
        return False


# Module-level singleton so callers don't manage instances.
recorder = TurnMetrics()

# How often (in turns) to emit a health snapshot + run the p95 alert check. The
# accumulator is cumulative, so we emit per-event warnings on failure (below)
# and a periodic rollup rather than alerting on every record after the first
# failure (which would spam, since counts never reset).
SNAPSHOT_EVERY = 20


def record(latency_seconds: float, outcome: Outcome) -> None:
    """Record a turn into the shared recorder singleton and surface health.

    Emission policy (so the metrics are actually observable, not silently
    accumulated): a failed turn (timeout/down) logs a WARNING immediately for
    this single event; every ``SNAPSHOT_EVERY`` turns we log an INFO rollup of
    the latency distribution and run :meth:`TurnMetrics.check_alert` so a p95
    regression is flagged before turns start crossing the wire deadline.
    """
    recorder.record(latency_seconds, outcome)

    if outcome in ("timeout", "down"):
        log.warning(
            "coach turn failed: outcome=%s latency=%.1fs", outcome, latency_seconds
        )

    snap = recorder.snapshot()
    if snap["count"] % SNAPSHOT_EVERY == 0:
        log.info(
            "coach turn metrics: count=%d p50=%s p95=%s p99=%s outcomes=%s",
            snap["count"], snap["p50"], snap["p95"], snap["p99"], snap["outcomes"],
        )
        recorder.check_alert()
