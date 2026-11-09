"""Plain-Python DAG walker for pipeline v2 (spec 2026-11-09-session-pipeline-v2 §5.2-5.4).

Import only after the profile env and dry_run.activate: pipeline.graph imports every node module.
"""
from __future__ import annotations

import random
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

from pipeline import graph, hitl, llm
from pipeline.graph import _RETRY_POLICY, _error_text, _instrumented, end_result
from pipeline.nodes.kb_curator import curate
from pipeline.run_common import (
    _APPROVAL_TITLES,
    _classify_non_recoverable,
    _format_error_prompt,
    _format_proposal,
    _refuse_unattended_curator,
    _write_run_record,
)
from pipeline.telegram import send as telegram_send
from pipeline.v2 import session
from pipeline.v2.store import Store

# Node: predecessors. Same DAG as pipeline/graph.py:build_graph, joins fire once (spec §1, §5.2).
TOPOLOGY: Dict[str, Tuple[str, ...]] = {
    "transcribe": (),
    "extract": ("transcribe",),
    "kb_curator": ("extract",),
    "extract_image": ("extract",),
    "pre_validator": ("kb_curator",),
    "writer": ("pre_validator",),
    "research": ("pre_validator",),
    "notion_blog": ("writer",),
    "youtube_metadata": ("writer",),
    "youtube_upload": ("youtube_metadata",),
    "notion_research": ("research",),
    "wordpress_draft": ("notion_blog", "extract_image"),
    "bv_wordpress": ("wordpress_draft",),
    "bv_research": ("notion_research",),
    "bv_youtube": ("youtube_upload",),
    "summarizer": ("bv_wordpress", "bv_research", "bv_youtube"),
}
RETRIED = frozenset({"transcribe", "extract", "kb_curator", "writer", "research", "notion_blog", "notion_research"})
REDUCERS = frozenset({"metrics", "branch_validations_done"})  # Annotated[..., add] in pipeline/state.py

_sleep = time.sleep  # tests patch this


def merge(state: Dict[str, Any], out: Optional[Dict[str, Any]]) -> None:
    for key, value in (out or {}).items():
        state[key] = [*(state.get(key) or []), *(value or [])] if key in REDUCERS else value


def _backoff(failures: int) -> float:
    p = _RETRY_POLICY
    delay = min(p.max_interval, p.initial_interval * p.backoff_factor ** (failures - 1))
    return delay + (random.uniform(0, 1) if p.jitter else 0)


def _node_fn(name: str) -> Callable[[Dict[str, Any]], Dict[str, Any]]:
    return getattr(graph, f"node_{name}")  # looked up per call so tests can patch pipeline.graph.node_<name>


class Orchestrator:
    def __init__(self, run_id: str, run_dir: Path, store: Store, *, auto_approve: bool = False,
                 telegram_on_error: bool = False, reminder_interval: float = 1800):
        self.run_id, self.run_dir, self.store = run_id, Path(run_dir), store
        self.auto_approve, self.telegram_on_error = auto_approve, telegram_on_error
        self.reminder_interval = reminder_interval
        self.hitl_wait = 0.0
        self.approvals = 0
        self._ask_lock = threading.Lock()  # one mailbox per run: never two questions at once
        self._step = threading.local()

    # --- walking -------------------------------------------------------------------------------------------------

    def walk(self, state: Dict[str, Any], start_at: str = "transcribe") -> Dict[str, Any]:
        nodes = [n for n in TOPOLOGY if start_at == "transcribe" or n != "transcribe"]
        preds = {n: [p for p in TOPOLOGY[n] if p in nodes] for n in nodes}
        done: set = set()
        running: Dict[Any, str] = {}
        with ThreadPoolExecutor(max_workers=len(nodes)) as pool:
            while len(done) < len(nodes):
                for n in nodes:
                    if n not in done and n not in running.values() and all(p in done for p in preds[n]):
                        running[pool.submit(self.run_node, n, dict(state))] = n
                finished, _ = wait(running, return_when=FIRST_COMPLETED)
                for future in finished:
                    name = running.pop(future)
                    merge(state, future.result())  # raises: the run aborts, in-flight nodes finish first
                    done.add(name)
        return state

    def run_node(self, name: str, state: Dict[str, Any]) -> Dict[str, Any]:
        """One node in its worker thread, with v1's transient retries and then the error ask (spec §5.2, §5.3)."""
        fn = (lambda s: curate(s, self.approve)) if name == "kb_curator" else _node_fn(name)
        wrapped = _instrumented(name, fn)
        failures = 0
        while True:
            try:
                return self._attempt(name, wrapped, state)
            except Exception as exc:  # noqa: BLE001
                failures += 1
                if name in RETRIED and failures < _RETRY_POLICY.max_attempts and _RETRY_POLICY.retry_on(exc):
                    _sleep(_backoff(failures))
                    continue
                if not self.ask_error(name, exc):
                    raise
                failures = 0

    def _attempt(self, name: str, wrapped: Callable, state: Dict[str, Any]) -> Dict[str, Any]:
        attempt = graph._ATTEMPTS.get((str(state.get("run_dir")), name), 0) + 1  # the number _instrumented will use
        step_id = self.store.start_step(self.run_id, name, attempt, state.keys())
        self._step.id = step_id
        backend = session.session_backend(lambda res, req: self._record_session(step_id, res, req),
                                          cwd=str(self.run_dir))
        try:
            with llm.backend(backend):  # bound here: a pool thread does not inherit the caller's contextvars
                out = wrapped(state)
        except Exception as exc:
            self.store.end_step(step_id, "error", error=_error_text(exc))
            raise
        self.store.end_step(step_id, "ok" if out else "noop", state_out=end_result(out))
        self.store.record_artifacts(self.run_id, step_id, out)
        return out or {}

    def _record_session(self, step_id: Optional[int], res: session.SessionResult, req: Dict[str, Any]) -> None:
        self.store.record_session(
            self.run_id, step_id, system_prompt=req["system_prompt"], user_message=req["user_message"],
            model=res.model, session_id=res.session_id, allowed_tools=req["tools"],
            max_turns=session.RESEARCH_MAX_TURNS if req["tools"] else 1, max_tokens=req["max_tokens"],
            text=res.text, messages=res.messages, input_tokens=res.input_tokens, output_tokens=res.output_tokens,
            cache_read_tokens=res.cache_read_tokens, cache_creation_tokens=res.cache_creation_tokens,
            notional_cost_usd=res.notional_cost_usd, duration_s=res.duration_s, num_turns=res.num_turns,
            stop_reason=res.stop_reason, sdk_version=res.sdk_version, cli_version=res.cli_version)

    # --- asking a human ------------------------------------------------------------------------------------------

    def _event(self, node: str, event: str) -> None:
        attempt = graph._ATTEMPTS.get((str(self.run_dir), node), 1)
        graph._emit(str(self.run_dir), {"ts": time.time(), "node": node, "attempt": attempt, "event": event})

    def _ask(self, kind: str, request: Dict[str, Any], status: str, **kw: Any) -> Optional[str]:
        with self._ask_lock:
            hid = self.store.record_hitl_ask(self.run_id, getattr(self._step, "id", None), kind, request)
            t0 = time.time()
            _write_run_record(self.run_dir, status=status)
            reply = hitl.ask(self.run_dir, request, self.reminder_interval, **kw)
            self.hitl_wait += time.time() - t0
            _write_run_record(self.run_dir, status="running", hitl_wait_s=self.hitl_wait)
            self.store.record_hitl_reply(hid, reply or "")
            return reply

    def approve(self, payload: Dict[str, Any]) -> str:
        """kb_curator's `ask` (spec §5.4): `_instrumented` only emits `paused` for GraphInterrupt, so emit it here."""
        self.approvals += 1
        print(f"[v2] HITL approval #{self.approvals}: {payload.get('kind')}")
        self._event("kb_curator", "paused")
        kind = str(payload.get("kind", "?"))
        request = hitl.approval_request("kb_curator", _APPROVAL_TITLES.get(kind, f"{kind}: approval needed"),
                                        _format_proposal(payload))
        if self.auto_approve:
            _refuse_unattended_curator(payload, self.approvals)
            hid = self.store.record_hitl_ask(self.run_id, getattr(self._step, "id", None), "approval", request)
            self.store.record_hitl_reply(hid, "yes")
            reply = "yes"
        else:
            reply = self._ask("approval", request, "waiting_hitl") or ""
        print(f"[v2] resume reply: {reply!r}")
        self._event("kb_curator", "start")
        return reply

    def ask_error(self, name: str, exc: Exception) -> bool:
        """True: re-run the node. Follows pipeline/run.py:_drive_graph (spec §5.3)."""
        print(f"[v2] pipeline raised in {name}: {type(exc).__name__}: {exc}")
        reason = _classify_non_recoverable(exc)
        prompt = _format_error_prompt(name, exc, reason)
        if self.auto_approve:
            if self.telegram_on_error:
                rc = telegram_send(prompt)
                if rc != 0:
                    print(f"[v2] telegram-on-error send rc={rc} (non-fatal)")
            return False
        reply = self._ask("error", hitl.error_request(name, _error_text(exc), prompt, reason), "waiting_reply",
                          max_reminders=5)
        print(f"[v2] error-recovery reply: {reply!r}")
        return bool(reply) and reply.strip().lower().startswith("retry") and not reason
