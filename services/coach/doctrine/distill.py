"""Extract de-personalised principles from a raw vault entry.

One LLM call per entry (the subscription seam ``call_llm``, monkeypatched in
tests). Every extracted principle passes the ``is_depersonalized`` QA gate before
it's kept — the gate, not the prompt, is the guarantee that biography never
reaches the doctrine corpus.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Optional

from eval.llm import call_llm

# Word-boundary first-person / author markers. Their presence means the text is
# autobiography, not transferable doctrine -> reject. Second person ("you") and
# impersonal statements are fine and intentionally NOT listed here.
_FIRST_PERSON = [
    r"\bI\b", r"\bI'?m\b", r"\bI'?ve\b", r"\bI'?ll\b", r"\bI'?d\b",
    r"\bmy\b", r"\bme\b", r"\bmine\b", r"\bmyself\b",
    r"\bwe\b", r"\bwe'?ve\b", r"\bwe'?re\b", r"\bour\b", r"\bus\b",
    # Spanish first person
    r"\byo\b", r"\bmi\b", r"\bmis\b", r"\bmí\b", r"\bme\b", r"\bconmigo\b",
    r"\bnosotros\b", r"\bnuestro\b", r"\bnuestra\b",
    # the author by name
    r"\bgonzalo\b",
]
_FIRST_PERSON_RE = re.compile("|".join(_FIRST_PERSON), re.IGNORECASE)

_SYSTEM = (
    "You distill a runner-philosopher's reflective journal entry into transferable "
    "COACHING PRINCIPLES — the lessons a coach could apply to ANY athlete.\n"
    "STRICT RULES:\n"
    "1. Each principle must be DE-PERSONALISED: no first person (I/me/my/we), no "
    "names, no dates, no specific autobiographical events. State the transferable "
    "lesson, not what happened to the author.\n"
    "2. Impersonal or generalised second person ('you') is fine.\n"
    "3. Drop anything that is purely biographical with no transferable lesson.\n"
    "4. Keep the author's distinctive concepts and framing, just stripped of "
    "personal history.\n"
    'Return ONLY JSON: {"principles":[{"text":"...","theme":"<short-kebab or empty>"}]}'
)


@dataclass
class Principle:
    text: str
    theme: str = ""
    source_slug: str = ""
    id: str = field(default="")


def is_depersonalized(text: str) -> bool:
    """True if ``text`` carries no first-person / author-name markers."""
    return not _FIRST_PERSON_RE.search(text or "")


def extract_with_stats(
    text: str, *, llm_fn=None, model: str = "claude-sonnet-4-6", source_slug: str = ""
) -> tuple[list[Principle], int]:
    """Distill one entry → ``(kept_principles, dropped_count)`` in ONE LLM call.

    ``dropped`` is how many non-empty principles the model proposed that the QA
    gate rejected (first-person / biography). Returns ``([], 0)`` on any failure.
    """
    if not (text or "").strip():
        return [], 0
    llm_fn = llm_fn or call_llm
    try:
        raw = llm_fn(system=_SYSTEM, user=text, model=model)
    except Exception:
        return [], 0
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end == -1 or end < start:
        return [], 0
    try:
        data = json.loads(raw[start : end + 1])
    except json.JSONDecodeError:
        return [], 0
    kept: list[Principle] = []
    proposed = 0
    for i, p in enumerate(data.get("principles", [])):
        ptext = str(p.get("text", "")).strip()
        if not ptext:
            continue
        proposed += 1
        if not is_depersonalized(ptext):
            continue  # QA gate: drop contaminated principles
        kept.append(
            Principle(
                text=ptext,
                theme=str(p.get("theme", "")).strip(),
                source_slug=source_slug,
                id=f"{source_slug or 'p'}-{i}",
            )
        )
    return kept, proposed - len(kept)


def extract_principles(
    text: str, *, llm_fn=None, model: str = "claude-sonnet-4-6", source_slug: str = ""
) -> list[Principle]:
    """Distill one entry into de-personalised principles (QA-gated)."""
    kept, _ = extract_with_stats(text, llm_fn=llm_fn, model=model, source_slug=source_slug)
    return kept
