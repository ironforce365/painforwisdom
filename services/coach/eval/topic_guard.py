"""Coaching-topic + no-file-download guardrail (F4).

A lightweight second classifier that runs alongside the grounding judge. It
inspects the coach's *drafted reply* and answers two yes/no questions:

  1. on_topic   — is this a coaching reply (goals, habits, training, mindset,
                  accountability) rather than off-topic chit-chat, code, trivia,
                  or general assistant Q&A?
  2. offers_file — does it offer the user a file, download, attachment, or
                  link to fetch one? (The pilot must never receive files.)

When the guard is ON and a reply fails either check, the reply is replaced with
a short localized redirect that steers back to coaching. The guard is a content
filter, not a generator: a clean reply passes through byte-for-byte.

Design contract (mirrors eval/grounding/integration.py):
- Default OFF via COACH_TOPIC_GUARD; inert when unset.
- Fail-safe: a malformed verdict or any LLM error is treated as clean, so the
  guard can never break a live turn — at worst it lets a reply through ungated.
- Single LLM seam ``call_llm`` (module global) so tests monkeypatch offline.

This runs in *parallel* with the grounding gate at the service boundary
(asyncio.gather of two to_thread calls), so it adds no serial latency to the
already-slow turn — both classifiers are in flight at once.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass

from eval.llm import call_llm

_LOG = logging.getLogger(__name__)
_ON_VALUES = {"1", "true", "on", "yes"}

_SYSTEM = (
    "You are a strict content classifier guarding a personal COACHING assistant. "
    "You are given the assistant's drafted reply to a user. Decide two things:\n"
    "1. on_topic: true if the reply is coaching — about the user's goals, habits, "
    "training, motivation, mindset, accountability, or reflective questions toward "
    "those. false if it is off-topic: general trivia, coding/technical help, "
    "current events, or anything unrelated to coaching the user toward their goals.\n"
    "2. offers_file: true if the reply offers, attaches, or links to a file, "
    "document, download, PDF, spreadsheet, or any downloadable artifact. "
    "Otherwise false.\n"
    "Respond with ONLY a JSON object: "
    '{"on_topic": bool, "offers_file": bool, "reason": "<short>"}'
)


@dataclass(frozen=True)
class TopicVerdict:
    on_topic: bool
    offers_file: bool
    reason: str = ""


def guard_enabled() -> bool:
    return os.environ.get("COACH_TOPIC_GUARD", "").strip().lower() in _ON_VALUES


def classify_topic(reply: str) -> TopicVerdict:
    """Classify a drafted reply. Fail-safe: any error/garbled output → clean."""
    try:
        raw = call_llm(system=_SYSTEM, user=reply)
        data = json.loads(_extract_json(raw))
        return TopicVerdict(
            on_topic=bool(data.get("on_topic", True)),
            offers_file=bool(data.get("offers_file", False)),
            reason=str(data.get("reason", "")),
        )
    except Exception:  # noqa: BLE001 - a guard bug must never block a turn
        _LOG.exception("topic guard classify failed; treating reply as clean")
        return TopicVerdict(on_topic=True, offers_file=False, reason="failsafe")


def _extract_json(raw: str) -> str:
    """Pull the first {...} object out of the model text (it may add prose)."""
    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("no JSON object in classifier output")
    return raw[start : end + 1]


# Localized redirect shown when a reply is blocked. Keyed off the user's
# Telegram language_code (same convention as telegram_bot/i18n.py).
_REDIRECT = {
    "en": (
        "Let's keep this about your coaching. I can't help with that or send "
        "files — but tell me what you're working toward and we'll dig in."
    ),
    "es": (
        "Sigamos enfocados en tu coaching. No puedo ayudarte con eso ni enviar "
        "archivos — pero cuéntame qué quieres lograr y seguimos trabajando."
    ),
    "pt": (
        "Vamos manter o foco no seu coaching. Não posso ajudar com isso nem "
        "enviar arquivos — mas me conte o que você quer conquistar e seguimos."
    ),
}


def _redirect_text(language_code: str | None) -> str:
    lang = (language_code or "").split("-", 1)[0].strip().lower()
    if lang in _REDIRECT:
        return _REDIRECT[lang]
    return f"{_REDIRECT['en']}\n\n{_REDIRECT['es']}"


def maybe_guard(reply: str, *, language_code: str | None = None) -> str:
    """Return ``reply`` if clean, else a localized coaching redirect.

    No-op when the guard flag is off. Fail-safe inside ``classify_topic``."""
    if not guard_enabled():
        return reply
    verdict = classify_topic(reply)
    if verdict.on_topic and not verdict.offers_file:
        return reply
    _LOG.info(
        "topic guard blocked reply (on_topic=%s offers_file=%s): %s",
        verdict.on_topic, verdict.offers_file, verdict.reason,
    )
    return _redirect_text(language_code)
