"""python -m pipeline.v2.run end to end over --from-transcript: real extract and kb_curator through the session
backend with FakeSession, a temp vault, the nodes around them stubbed as in tests/test_real_graph_hitl.py's Rig
(spec 2026-11-09-session-pipeline-v2 §5.5). Integration: kb_curator writes a vault.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter

import pytest
import yaml

from pipeline import graph, hitl, telegram
from pipeline.nodes import kb_curator
from pipeline.v2 import orchestrator, run, session

pytestmark = pytest.mark.integration

THEME = {"action": "NEEDS_APPROVAL_THEME", "proposed_theme": "theme-a", "reason": "r", "core_tension": "t"}
PROCEED = {"action": "PROCEED", "entry_slug": "entry-a", "entry_body": "body\n", "timeline_row": "| entry-a |",
           "themes_attached": ["theme-a"], "theme_updates": [{"slug": "theme-a", "new_body": "# theme-a\n"}]}
EXTRACTION = "## Content Quality\nHIGH\n\n## Core Insight\nPain teaches.\n"


def _plan(plan):
    return f"---kb-plan---\n{yaml.safe_dump(plan)}\n---kb-plan---"


def test_from_transcript_run_json_events_and_store_agree(tmp_path, monkeypatch):
    vault = tmp_path / "vault" / "gonzalo-book"
    monkeypatch.setattr(kb_curator, "VAULT_ROOT", vault)
    monkeypatch.setenv("PIPELINE_V2_DB", str(tmp_path / "v2.db"))
    monkeypatch.delenv("PIPELINE_DRY_RUN", raising=False)
    monkeypatch.setattr(telegram, "send", lambda *a, **k: 0)
    fake = session.FakeSession(EXTRACTION, _plan(THEME), _plan(PROCEED))
    monkeypatch.setattr(session, "run_session", fake)
    asked = []
    monkeypatch.setattr(hitl, "ask", lambda run_dir, request, interval, **kw: asked.append(request) or "yes")
    for name in ("extract_image", "pre_validator", "writer", "research", "notion_blog", "notion_research",
                 "youtube_metadata", "youtube_upload", "wordpress_draft", "bv_research", "bv_wordpress", "bv_youtube"):
        out = {"branch_validations_done": [name[3:]]} if name.startswith("bv_") else {}
        monkeypatch.setattr(graph, f"node_{name}", lambda state, out=out: out)
    monkeypatch.setattr(graph, "node_summarizer", lambda s: {"validator_verdict": "PASS"})

    transcript = tmp_path / "2026-11-05-run.txt"
    transcript.write_text("I ran and it hurt.")
    args = argparse.Namespace(video=None, from_transcript=str(transcript), profile="sandbox", run_id=None,
                              language=None, dry_run=False, processed_root=tmp_path / "processed",
                              reminder_interval=600, auto_approve=False, telegram_on_error=False)
    assert run.process(args, "rid") == 0

    run_dir = tmp_path / "processed" / "rid" / "langgraph"
    rec = json.loads((run_dir / "run.json").read_text())
    assert (rec["status"], rec["verdict"], rec["engine"], rec["error"]) == ("done", "PASS", "sessions", None)
    assert sorted(p.stem for p in (vault / "entries").glob("*.md")) == ["entry-a"]
    assert len(asked) == 1 and asked[0]["node"] == "kb_curator"
    assert all(c["cwd"] == str(run_dir) for c in fake.calls)

    events = [json.loads(line) for line in (run_dir / "events.jsonl").read_text().splitlines()]
    assert [e["event"] for e in events if e["node"] == "kb_curator"] == ["start", "paused", "start", "end"]
    db = sqlite3.connect(tmp_path / "v2.db")
    steps = Counter(n for (n,) in db.execute("SELECT node FROM steps WHERE run_id='rid' AND status IN ('ok','noop')"))
    assert steps == Counter(e["node"] for e in events if e["event"] == "end")
    assert set(steps) == set(orchestrator.TOPOLOGY) - {"transcribe"}
    assert db.execute("SELECT status, verdict, engine, input_kind FROM runs").fetchall() == [
        ("done", "PASS", "sessions", "transcript")]
    sessions = db.execute("SELECT st.node FROM sessions s JOIN steps st ON st.id = s.step_id ORDER BY s.id").fetchall()
    assert sessions == [("extract",), ("kb_curator",), ("kb_curator",)]
    assert db.execute("SELECT kind, reply FROM hitl").fetchall() == [("approval", "yes")]
