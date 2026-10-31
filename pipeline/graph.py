"""LangGraph DAG for the content pipeline.

Topology (per-branch validators + summarizer join):

    START → transcribe → extract ─┬─▶ kb_curator → pre_validator ─┬─▶ writer ─▶ notion_blog ─┐
                                  │                               └─▶ research ─▶ notion_research ─▶ bv_research ──┐
                                  ├─▶ extract_image ──────────────────────────────────────────┐                    │
                                  └─▶ youtube_upload ─▶ bv_youtube ────────────────────────────┼────────────────────┤
                                                                                               ▼                    │
                                                                                       wordpress_draft ─▶ bv_wordpress ─▶ summarizer → END
                                                                                                                          ▲
                                                                                                                          │
                                                                                                                bv_research ┘

`extract` fans out to three siblings: ``kb_curator`` (the existing
writer/research lineage), ``extract_image`` (smart-frame featured image),
and ``youtube_upload`` (draft short on YouTube). LangGraph synchronises
on join nodes via disjoint state keys.

`pre_validator` runs once after kb_curator and owns the shared upstream
checks (transcript, extraction, vault entry, themes/frameworks, timeline).
Each terminal branch then has its own validator (``bv_research``,
``bv_wordpress``, ``bv_youtube``) checking only the artifacts that branch
produced.

`summarizer` is a join with three inbound edges. LangGraph fires it once
per parent completion (3 fires per run); the node is idempotent on the
first 2 fires (returns {} without side effects) and only aggregates +
writes the audit report + sends Telegram on the third (final) fire when
all branch validators have reported.

Checkpointing: SqliteSaver — durable across HITL `interrupt()` from kb_curator.

Per-node retry: every functional node has a `RetryPolicy` that classifies
transient infra errors (network, rate limit, 5xx) as retryable up to 3x with
exponential backoff. Persistent errors (`InputContractError`, value/logic
errors) are NOT retried — they bubble to the orchestrator in `run.py`, which
asks Gonzalo via Telegram what to do.
"""
from __future__ import annotations

import functools
import json
import os
import socket
import sqlite3
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, Tuple

import httpx
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.errors import GraphInterrupt
from langgraph.graph import END, START, StateGraph
from langgraph.types import RetryPolicy

from pipeline.contracts import InputContractError
from pipeline.nodes.extract import node_extract
from pipeline.nodes.extract_image import node_extract_image
from pipeline.nodes.kb_curator import node_kb_curator
from pipeline.nodes.notion_blog import node_notion_blog
from pipeline.nodes.notion_research import node_notion_research
from pipeline.nodes.research import node_research
from pipeline.nodes.transcribe import node_transcribe
from pipeline.nodes.validators.branch_research import node_bv_research
from pipeline.nodes.validators.branch_wordpress import node_bv_wordpress
from pipeline.nodes.validators.branch_youtube import node_bv_youtube
from pipeline.nodes.validators.pre import node_pre_validator
from pipeline.nodes.validators.summarizer import node_summarizer
from pipeline.nodes.wordpress_draft import node_wordpress_draft
from pipeline.nodes.writer import node_writer
from pipeline.nodes.youtube_upload import node_youtube_upload
from pipeline.state import State

PROJECT_ROOT = Path(__file__).resolve().parent.parent
# Sandbox profile redirects the checkpoint DB so HITL/interrupt state from
# test runs doesn't bleed into prod resume semantics.
CHECKPOINT_DB = Path(os.environ.get("CHECKPOINT_DB_PATH") or (PROJECT_ROOT / "pipeline" / "checkpoints.db"))


def _is_transient(exc: Exception) -> bool:
    """Classifier for RetryPolicy.retry_on. True = retry, False = give up.

    Keep this conservative: only retry things that are demonstrably infra
    failures. A `RuntimeError` from a node usually means a structural
    problem (bad model output, missing field) — retrying just burns tokens.
    """
    # Never retry contract violations — upstream produced no useful state.
    if isinstance(exc, InputContractError):
        return False
    # Network / HTTP transients
    if isinstance(exc, (httpx.HTTPError, socket.timeout, ConnectionError)):
        return True
    if isinstance(exc, subprocess.TimeoutExpired):
        return True
    # litellm errors carry status/code; lean on their class names since the
    # SDK exception hierarchy varies across versions.
    cls = type(exc).__name__
    if cls in {
        "RateLimitError",
        "APIConnectionError",
        "APITimeoutError",
        "ServiceUnavailableError",
        "InternalServerError",
        "Timeout",
    }:
        return True
    # notion-client APIResponseError with 5xx status
    if cls == "APIResponseError":
        status = getattr(exc, "status", None) or getattr(exc, "code", None)
        try:
            return int(status) >= 500
        except (TypeError, ValueError):
            return False
    # Anything else (RuntimeError, ValueError, AttributeError, etc.) is
    # treated as persistent — orchestrator decides.
    return False


# Node lifecycle events (C1): docs/superpowers/specs/2026-10-31-pipeline-console-ui.md section 2.
_EVENTS_LOCK = threading.Lock()
_ATTEMPTS: Dict[Tuple[str, str], int] = {}


def _error_text(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"[:500]


def _emit(run_dir: str, row: Dict[str, Any]) -> None:
    line = json.dumps(row) + "\n"
    with _EVENTS_LOCK, open(Path(run_dir) / "events.jsonl", "a") as f:
        f.write(line)


def _instrumented(name: str, fn: Callable[[Any], Dict[str, Any]]) -> Callable[[Any], Dict[str, Any]]:
    @functools.wraps(fn)
    def wrapper(state):
        run_dir = state.get("run_dir")
        if not run_dir:
            return fn(state)
        with _EVENTS_LOCK:
            attempt = _ATTEMPTS.get((run_dir, name), 0) + 1
            _ATTEMPTS[(run_dir, name)] = attempt
        base = {"node": name, "attempt": attempt}
        _emit(run_dir, {"ts": time.time(), **base, "event": "start"})
        t0 = time.time()
        try:
            out = fn(state)
        except GraphInterrupt:
            _emit(run_dir, {"ts": time.time(), **base, "event": "paused"})
            raise
        except Exception as exc:
            _emit(run_dir, {"ts": time.time(), **base, "event": "error", "error": _error_text(exc)})
            raise
        outputs = {k: v for k, v in (out or {}).items() if k.endswith("_path") and isinstance(v, str) and v}
        _emit(run_dir, {
            "ts": time.time(), **base, "event": "end",
            "duration_s": time.time() - t0, "noop": not out, "outputs": outputs,
        })
        return out

    return wrapper


_RETRY_POLICY = RetryPolicy(
    initial_interval=2.0,
    backoff_factor=2.0,
    max_interval=60.0,
    max_attempts=3,
    jitter=True,
    retry_on=_is_transient,
)


def build_graph(start_at: str = "transcribe"):
    """Build and compile the pipeline.

    `start_at` is either "transcribe" (full pipeline from a video file) or
    "extract" (test mode: caller pre-injects transcript_text, transcript_path,
    video_date into state and skips Whisper).

    Returns a compiled graph + the SqliteSaver instance so the caller can
    close the SQLite connection on shutdown.
    """
    if start_at not in {"transcribe", "extract"}:
        raise ValueError(f"start_at must be 'transcribe' or 'extract', got {start_at!r}")

    g = StateGraph(State)
    g.add_node("transcribe", _instrumented("transcribe", node_transcribe), retry_policy=_RETRY_POLICY)
    g.add_node("extract", _instrumented("extract", node_extract), retry_policy=_RETRY_POLICY)
    g.add_node("kb_curator", _instrumented("kb_curator", node_kb_curator), retry_policy=_RETRY_POLICY)
    g.add_node("writer", _instrumented("writer", node_writer), retry_policy=_RETRY_POLICY)
    g.add_node("research", _instrumented("research", node_research), retry_policy=_RETRY_POLICY)
    g.add_node("notion_blog", _instrumented("notion_blog", node_notion_blog), retry_policy=_RETRY_POLICY)
    g.add_node("notion_research", _instrumented("notion_research", node_notion_research), retry_policy=_RETRY_POLICY)
    # New nodes (no retry policy — they catch their own errors and degrade
    # gracefully instead of bubbling exceptions into the graph).
    g.add_node("extract_image", _instrumented("extract_image", node_extract_image))
    g.add_node("youtube_upload", _instrumented("youtube_upload", node_youtube_upload))
    g.add_node("wordpress_draft", _instrumented("wordpress_draft", node_wordpress_draft))
    # Per-branch validators + summarizer join. None need retry: they only
    # inspect state and (for summarizer) send Telegram with internal retry.
    g.add_node("pre_validator", _instrumented("pre_validator", node_pre_validator))
    g.add_node("bv_research", _instrumented("bv_research", node_bv_research))
    g.add_node("bv_wordpress", _instrumented("bv_wordpress", node_bv_wordpress))
    g.add_node("bv_youtube", _instrumented("bv_youtube", node_bv_youtube))
    g.add_node("summarizer", _instrumented("summarizer", node_summarizer))

    g.add_edge(START, start_at)
    if start_at == "transcribe":
        g.add_edge("transcribe", "extract")
    g.add_edge("extract", "kb_curator")
    g.add_edge("extract", "extract_image")
    g.add_edge("extract", "youtube_upload")
    # pre_validator runs once between kb_curator and the writer/research fan-out
    # so shared upstream findings are computed exactly once.
    g.add_edge("kb_curator", "pre_validator")
    g.add_edge("pre_validator", "writer")
    g.add_edge("pre_validator", "research")
    g.add_edge("writer", "notion_blog")
    g.add_edge("research", "notion_research")
    g.add_edge("notion_blog", "wordpress_draft")
    g.add_edge("extract_image", "wordpress_draft")
    # Per-branch validators at the tail of each terminal branch.
    g.add_edge("wordpress_draft", "bv_wordpress")
    g.add_edge("notion_research", "bv_research")
    g.add_edge("youtube_upload", "bv_youtube")
    # Summarizer is a join: 3 inbound edges. LangGraph fires it once per parent
    # completion; the node is idempotent on early fires (see summarizer.py).
    g.add_edge("bv_wordpress", "summarizer")
    g.add_edge("bv_research", "summarizer")
    g.add_edge("bv_youtube", "summarizer")
    g.add_edge("summarizer", END)

    CHECKPOINT_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(CHECKPOINT_DB), check_same_thread=False)
    saver = SqliteSaver(conn)
    return g.compile(checkpointer=saver), saver
