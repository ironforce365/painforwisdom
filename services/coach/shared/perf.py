"""Lightweight per-step timing for the coach turn pipeline.

Why: since the grounding gate went on (v0.1.5+), a `/turn` does several
synchronous LLM-bound steps (doctrine retrieve, mem0 read, agent answer, claim
judge, mem0 write). To know which one to optimise we measure each, rather than
guess. `perf_step` brackets a block and emits ONE structured line per step to
the `coach.perf` logger (stdout → `docker logs coach-coach-agent`):

    perf step=gate_judge ms=18342 gate=True

Design constraints:
- Zero external deps (stdlib only) — safe to import from both `agent` and `eval`.
- Always-on and cheap (a monotonic clock read + one log line per step).
- Never breaks a turn: the wrapped body's exception propagates, but only AFTER
  the timing line is emitted, and the logging itself can never raise.
"""
from __future__ import annotations

import logging
import sys
import time
from contextlib import contextmanager

_LOG = logging.getLogger("coach.perf")


def _ensure_handler() -> None:
    """Attach a stdout INFO handler once, so perf lines surface in container logs
    regardless of how uvicorn configured the root logger. Idempotent; own handler
    + propagate=False avoids double-emission through the root logger."""
    if _LOG.handlers:
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(asctime)s %(name)s %(message)s"))
    _LOG.addHandler(handler)
    _LOG.setLevel(logging.INFO)
    _LOG.propagate = False


_ensure_handler()


@contextmanager
def perf_step(step: str, **fields):
    """Time a block and log ``perf step=<step> ms=<elapsed> [k=v ...]``.

    A sync context manager is intentional: it brackets `async with`/`await`
    bodies fine (the elapsed wall-clock includes awaited time), and stays usable
    from synchronous call sites like the gate's subprocess judge.
    """
    t0 = time.monotonic()
    err = ""
    try:
        yield
    except BaseException as exc:  # record the failure, then re-raise unchanged
        err = type(exc).__name__
        raise
    finally:
        ms = int((time.monotonic() - t0) * 1000)
        extra = "".join(f" {k}={v}" for k, v in fields.items())
        if err:
            extra += f" error={err}"
        try:
            _LOG.info("perf step=%s ms=%d%s", step, ms, extra)
        except Exception:  # logging must never break the wrapped operation
            pass
