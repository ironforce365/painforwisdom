"""Adapter that plugs the grounding gate into the coach send-path.

Default OFF: with COACH_GROUNDING_GATE unset, the reply is returned untouched, so
the deployed coach behaves exactly as before. When ON, the gate runs; any failure
falls back to the ungated reply (a gate bug must never break a live turn).

``judge_claims`` and ``demote_to_question`` are imported as module globals so the
gate uses these references (and tests can monkeypatch them).
"""
from __future__ import annotations

import logging
import os

from .config import load_config
from .corpus import RegressionCorpus
from .gate import run_gate
from .judge import judge_claims
from .rewriter import demote_to_question
from .types import Source

_LOG = logging.getLogger(__name__)
_ON_VALUES = {"1", "true", "on", "yes"}


def gate_enabled() -> bool:
    return os.environ.get("COACH_GROUNDING_GATE", "").strip().lower() in _ON_VALUES


def maybe_gate(reply: str, *, sources: list[Source], user_id: str, thread_id: str) -> str:
    if not gate_enabled():
        return reply
    try:
        cfg = load_config()
        corpus_dir = os.environ.get("COACH_GROUNDING_CORPUS_DIR", "/data/grounding_corpus")
        result = run_gate(
            reply,
            sources,
            temperature=cfg.temperature,
            judge_fn=judge_claims,
            rewrite_fn=demote_to_question,
            corpus=RegressionCorpus(corpus_dir),
            user_id=user_id,
            thread_id=thread_id,
        )
        return result.message
    except Exception:  # noqa: BLE001 - never let the gate break a live turn
        _LOG.exception("grounding gate failed; falling back to ungated reply")
        return reply
