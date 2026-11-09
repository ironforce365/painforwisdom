"""Real Claude Agent SDK sessions (spec 2026-11-09-session-pipeline-v2 §2.5). Needs a Claude login."""
import os
import shutil

import pytest

from pipeline.nodes import research
from pipeline.v2 import session

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not os.environ.get("PIPELINE_V2_REAL_SDK") or not shutil.which("claude"),
                       reason="set PIPELINE_V2_REAL_SDK=1 with a logged-in claude CLI"),
]
MODEL = "claude-haiku-4-5"


def test_toolless_session(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-bogus")  # must be stripped, else the call fails auth
    res = session.run_session("Reply with exactly: pong", "ping", model=MODEL, max_turns=1, cwd=str(tmp_path))
    print(res.usage(), res.notional_cost_usd, res.num_turns, res.cli_version)
    assert "pong" in res.text.lower() and res.input_tokens > 0


def test_research_session_parses_csv(tmp_path):
    system = ("Use WebSearch to find two real books about endurance running. Answer ONLY a CSV with header "
              "Title,Author,Type,URL,Why and one row per book, inside a ```csv fence.")
    res = session.run_session(system, "Find them.", model=MODEL, allowed_tools=session.RESEARCH_TOOLS,
                              max_turns=session.RESEARCH_MAX_TURNS, cwd=str(tmp_path))
    rows = research._parse_csv(res.text)
    print(res.usage(), res.num_turns, rows)
    assert any(m["type"] == "AssistantMessage" and "WebSearch" in str(m) for m in res.messages)
    assert rows
