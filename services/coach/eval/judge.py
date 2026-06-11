"""LLM-as-judge (Sonnet 4.6) rubric scorer."""
from __future__ import annotations
import json
from pathlib import Path

_RUBRIC = (Path(__file__).parent / "rubric.md").read_text(encoding="utf-8")


def _call_judge_llm(system: str, user: str) -> str:
    # Subscription-backed (no API key), per memory: subscription_cli_judge.
    from eval.llm import call_llm

    return call_llm(system=system, user=user, model="claude-sonnet-4-6")


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
    if start == -1 or end == -1 or end < start:
        return {"error": "no_json", "raw": raw[:500]}
    try:
        return json.loads(raw[start:end + 1])
    except json.JSONDecodeError as e:
        return {"error": "json_decode", "raw": raw[start:end + 1][:500], "detail": str(e)}
