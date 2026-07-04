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
from datetime import datetime

from .config import load_config
from .corpus import RegressionCorpus
from .gate import run_gate
from .judge import judge_claims
from .rewriter import demote_to_question
from .segmenter import strip_claim_tags
from .types import Source
from .validation_detector import detect
from .validations import PendingValidations

_LOG = logging.getLogger(__name__)
_ON_VALUES = {"1", "true", "on", "yes"}

# detection outcome -> regression-corpus signal
_SIGNAL = {"confirmed": "validation", "corrected": "correction"}


def gate_enabled() -> bool:
    return os.environ.get("COACH_GROUNDING_GATE", "").strip().lower() in _ON_VALUES


def _corpus() -> RegressionCorpus:
    return RegressionCorpus(
        os.environ.get("COACH_GROUNDING_CORPUS_DIR", "/data/grounding_corpus")
    )


def _validations() -> PendingValidations:
    default = os.path.join(
        os.environ.get("COACH_GROUNDING_CORPUS_DIR", "/data/grounding_corpus"),
        "validations",
    )
    return PendingValidations(os.environ.get("COACH_VALIDATIONS_DIR", default))


def _now() -> str:
    # the runtime boundary owns the clock; eval modules never invent timestamps
    return datetime.now().isoformat(timespec="seconds")


def maybe_gate(
    reply: str, *, sources: list[Source], user_id: str, thread_id: str,
    persist: bool = True,
) -> str:
    """Run the grounding gate on a drafted reply.

    ``persist=False`` (synthetic test-channel turns) runs the gate identically —
    demotions and claim-tag handling included, so test replies stay
    representative — but writes nothing to the regression corpus or the pending
    validations: fabricated persona conversations must never feed the
    calibration data."""
    if not gate_enabled():
        return reply
    try:
        cfg = load_config()
        result = run_gate(
            reply,
            sources,
            temperature=cfg.temperature,
            judge_fn=judge_claims,
            rewrite_fn=demote_to_question,
            corpus=_corpus() if persist else None,
            validations=_validations() if persist else None,
            ts=_now(),
            user_id=user_id,
            thread_id=thread_id,
        )
        return result.message
    except Exception:  # noqa: BLE001 - never let the gate break a live turn
        _LOG.exception("grounding gate failed; falling back to ungated reply")
        # With the flag on, the coach emitted [[claim]] tags; strip them so a gate
        # error never leaks the internal protocol into the user's reply.
        return strip_claim_tags(reply)


def detect_validation_signals(user_text: str, *, thread_id: str, user_id: str) -> list[dict]:
    """Match an INCOMING user turn against the thread's open validations.

    Runs before the agent (Stream 2): confirmed reads log a ``validation``
    signal, corrections log a ``correction`` signal (the lived-contradiction
    bucket, captured in the user's own words), and both close the open item.
    Unaddressed items stay open for the next turn.

    Same fail-safe contract as ``maybe_gate``: flag off or any failure → ``[]``,
    never a broken turn.
    """
    if not gate_enabled():
        return []
    try:
        store = _validations()
        opens = store.list_open(thread_id)
        if not opens:
            return []
        outcomes = detect(user_text, opens)
        by_id = {i["claim_id"]: i for i in opens}
        corpus = _corpus()
        ts = _now()
        for o in outcomes:
            if o["outcome"] not in _SIGNAL:
                continue
            item = by_id[o["claim_id"]]
            corpus.append({
                "ts": ts,
                "signal": _SIGNAL[o["outcome"]],
                "claim_id": o["claim_id"],
                "claim_text": item.get("claim_text", ""),
                "claim_type": item.get("claim_type"),
                "confidence": item.get("confidence"),
                "user_correction": o.get("correction_text", ""),
                "thread_id": thread_id,
                "user_id": user_id,
                "source": "live_validation",
            })
            store.resolve(thread_id, o["claim_id"], status=o["outcome"], ts=ts,
                          note=o.get("correction_text", ""))
        return outcomes
    except Exception:  # noqa: BLE001 - detection must never break a live turn
        _LOG.exception("validation-signal detection failed; skipping")
        return []
