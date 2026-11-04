"""Per-call LLM usage log, C8 usage.jsonl (spec 2026-11-03-console-operability §4)."""
from __future__ import annotations

import contextvars
import json
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, Optional, Tuple

_BOUND: contextvars.ContextVar[Optional[Tuple[str, str, int]]] = contextvars.ContextVar("pipeline_usage", default=None)
_LOCK = threading.Lock()
_MODELS: Dict[str, str] = {}


@contextmanager
def bind(run_dir: str, node: str, attempt: int) -> Iterator[None]:
    token = _BOUND.set((run_dir, node, attempt))
    try:
        yield
    finally:
        _BOUND.reset(token)


def _write(event: str, call_id: str, model: str, **extra: Any) -> None:
    bound = _BOUND.get()
    if bound is None:
        return
    run_dir, node, attempt = bound
    line = json.dumps({"ts": time.time(), "event": event, "call_id": call_id, "node": node, "attempt": attempt,
                       "model": model, **extra}) + "\n"
    try:
        with _LOCK, open(Path(run_dir) / "usage.jsonl", "a") as f:
            f.write(line)
    except OSError:  # telemetry must never fail or retry an LLM call that already ran
        pass


def _close(call_id: str) -> str:
    with _LOCK:
        return _MODELS.pop(call_id, "")


def record_start(model: str) -> str:
    if _BOUND.get() is None:
        return ""
    call_id = uuid.uuid4().hex
    with _LOCK:
        _MODELS[call_id] = model
    _write("start", call_id, model)
    return call_id


def record_end(call_id: str, usage: Dict[str, int], cost_usd: float, call_s: float) -> None:
    _write("end", call_id, _close(call_id), **usage, cost_usd=cost_usd, call_s=call_s)


def record_error(call_id: str, exc: BaseException) -> None:
    _write("error", call_id, _close(call_id), error=f"{type(exc).__name__}: {exc}"[:500])
