"""kb_curator asks instead of crashing on an action it does not know (spec 2026-11-05-console-end-to-end, A5)."""
from __future__ import annotations

from typing import Any, Dict, List

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from pipeline.nodes import kb_curator
from pipeline.state import State

USAGE = {"duration_s": 1.0, "input_tokens": 1, "output_tokens": 1, "cache_read_tokens": 0, "cache_creation_tokens": 0,
         "cost_usd": 0.0}
ALREADY = {"action": "ALREADY_PROCESSED", "reason": "entry 2026-11-03-x exists for this video"}
CONFIG = {"configurable": {"thread_id": "t"}}


@pytest.fixture
def curator(monkeypatch):
    histories: List[list] = []
    applied: List[dict] = []
    plans: List[Dict[str, Any]] = []

    def fake_llm(state, approval_history):
        histories.append(list(approval_history))
        # interrupt() re-runs the node from its top: the plan depends on the answers so far, as with the real LLM.
        return plans[min(len(approval_history), len(plans) - 1)], USAGE

    monkeypatch.setattr(kb_curator, "_call_llm_for_plan", fake_llm)
    monkeypatch.setattr(kb_curator, "assert_inputs", lambda *a: None)
    monkeypatch.setattr(kb_curator, "append_metric", lambda *a, **k: None)
    monkeypatch.setattr(kb_curator, "_apply_proceed", lambda state, plan: applied.append(plan) or {"vault_entry_slug": "s"})
    g = StateGraph(State)
    g.add_node("kb_curator", kb_curator.node_kb_curator)
    g.add_edge(START, "kb_curator")
    g.add_edge("kb_curator", END)
    return g.compile(checkpointer=InMemorySaver()), plans, histories, applied


def _interrupt(graph) -> Dict[str, Any]:
    tasks = graph.get_state(CONFIG).tasks
    return tasks[0].interrupts[0].value


def test_already_processed_interrupts_and_reply_replans(curator):  # [DT-A5] [DT-A3]
    graph, plans, histories, applied = curator
    plans += [ALREADY, {"action": "PROCEED"}]
    graph.invoke({"run_dir": "x"}, config=CONFIG)
    payload = _interrupt(graph)
    assert payload["kind"] == "curator"
    assert "ALREADY_PROCESSED" in payload["proposal"] and "entry 2026-11-03-x exists" in payload["proposal"]
    assert payload["raw_plan"] == ALREADY
    assert not applied
    graph.invoke(Command(resume="skip the duplicate, keep the old entry"), config=CONFIG)
    assert applied == [{"action": "PROCEED"}]
    proposal, reply = histories[-1][0]
    assert "ALREADY_PROCESSED" in proposal and reply == "skip the duplicate, keep the old entry"


def test_unknown_action_may_ask_again(curator):  # [DT-A5]
    graph, plans, _, applied = curator
    plans += [ALREADY, ALREADY, {"action": "PROCEED"}]
    graph.invoke({"run_dir": "x"}, config=CONFIG)
    graph.invoke(Command(resume="what?"), config=CONFIG)
    assert _interrupt(graph)["kind"] == "curator" and not applied
    graph.invoke(Command(resume="proceed anyway"), config=CONFIG)
    assert applied


@pytest.mark.parametrize("plan", [{}, {"action": None}, {"action": 7}])
def test_missing_or_odd_action_asks_too(curator, plan):  # [DT-A5]
    graph, plans, _, _ = curator
    plans.append(plan)
    graph.invoke({"run_dir": "x"}, config=CONFIG)
    assert _interrupt(graph)["kind"] == "curator"


def test_known_approvals_keep_their_kind(curator):  # [DT-A1]
    graph, plans, _, _ = curator
    plans += [{"action": "NEEDS_APPROVAL_THEME", "new_theme_slug": "s", "definition": "d"}]
    graph.invoke({"run_dir": "x"}, config=CONFIG)
    assert _interrupt(graph)["kind"] == "theme"
