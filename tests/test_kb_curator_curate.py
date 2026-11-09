"""kb_curator.curate runs with a plain blocking ask, no LangGraph runtime (spec 2026-11-09-session-pipeline-v2 §4)."""
from __future__ import annotations

from typing import Any, Dict, List

from pipeline.nodes import kb_curator

USAGE = {"duration_s": 1.0, "llm_calls": 1, "input_tokens": 1, "output_tokens": 1, "cache_read_tokens": 0,
         "cache_creation_tokens": 0, "cost_usd": 0.0}
THEME = {"action": "NEEDS_APPROVAL_THEME", "proposed_theme": "t", "reason": "r", "definition": "d"}


def test_curate_with_plain_ask(monkeypatch, tmp_path):
    histories: List[list] = []
    applied: List[dict] = []
    asked: List[Dict[str, Any]] = []
    metrics: List[dict] = []
    plans = [THEME, {"action": "PROCEED"}]

    def fake_llm(state, approval_history):
        histories.append(list(approval_history))
        return plans[min(len(approval_history), 1)], USAGE

    monkeypatch.setattr(kb_curator, "_call_llm_for_plan", fake_llm)
    monkeypatch.setattr(kb_curator, "assert_inputs", lambda *a: None)
    monkeypatch.setattr(kb_curator, "append_metric", lambda *a, **k: metrics.append(k))
    monkeypatch.setattr(kb_curator, "_apply_proceed", lambda state, plan: applied.append(plan) or {"vault_entry_slug": "s"})

    out = kb_curator.curate({"run_dir": str(tmp_path)}, lambda payload: asked.append(payload) or "yes")

    assert out == {"vault_entry_slug": "s"}
    assert [p["kind"] for p in asked] == ["theme"] and asked[0]["raw_plan"] == THEME
    assert histories[-1] == [(asked[0]["proposal"], "yes")]
    assert applied == [{"action": "PROCEED"}]
    assert metrics[0]["approvals"] == 1
    assert [m["phase"] for m in metrics[0]["per_call"]] == ["initial", "resume_1"]
