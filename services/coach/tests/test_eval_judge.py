"""Judge parses a fixture transcript and returns rubric scores."""
from __future__ import annotations
from eval.judge import score_turn


def test_score_turn_returns_rubric_keys(monkeypatch):
    fake = '{"frontal":4,"no_citing":5,"probing":3,"brevity":4,"grounding":5,"voice":4,"reasoning":"ok"}'
    monkeypatch.setattr("eval.judge._call_judge_llm", lambda system, user: fake)
    result = score_turn(
        user_text="why do I always skip in winter?",
        coach_reply="What did you avoid this week, specifically?",
        retrieved=[{"text": "comfort default ...", "source": "comfort-as-default"}],
    )
    assert result["frontal"] == 4
    assert "reasoning" in result
