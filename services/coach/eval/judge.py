"""LLM-as-judge (Sonnet 4.6) rubric scorer."""
from __future__ import annotations
import json
import os
from pathlib import Path

_RUBRIC = (Path(__file__).parent / "rubric.md").read_text(encoding="utf-8")


def _call_judge_llm(system: str, user: str) -> str:
    import anthropic
    client = anthropic.Anthropic()
    resp = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=600,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return resp.content[0].text


def score_turn(*, user_text: str, coach_reply: str, retrieved: list[dict]) -> dict:
    retrieved_dump = "\n".join(f"- ({r['source']}) {r['text'][:300]}" for r in retrieved)
    user = (
        f"USER MESSAGE:\n{user_text}\n\n"
        f"COACH REPLY:\n{coach_reply}\n\n"
        f"RETRIEVED CHUNKS:\n{retrieved_dump}"
    )
    raw = _call_judge_llm(_RUBRIC, user)
    start = raw.find("{")
    end = raw.rfind("}")
    return json.loads(raw[start:end + 1])
