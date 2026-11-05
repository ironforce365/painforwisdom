"""HITL on the real graph: `pipeline.graph.build_graph` over SqliteSaver (temp DB), the real kb_curator and the real
C11 mailbox. Only the LLM, Telegram and the nodes around the curator are stubbed.

A fake runner cannot show these: they live in how LangGraph replays a node and reports an interrupted task.
"""
from __future__ import annotations

import argparse
import functools
import json
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import pytest
import yaml

os.environ.setdefault("CHECKPOINT_DB_PATH", str(Path(tempfile.mkdtemp()) / "checkpoints.db"))

from pipeline import graph as graph_mod  # noqa: E402
from pipeline import hitl, retry, run  # noqa: E402
from pipeline.nodes import kb_curator  # noqa: E402

pytestmark = pytest.mark.integration

RID = "rid"
THEME_A = {"action": "NEEDS_APPROVAL_THEME", "proposed_theme": "theme-a", "reason": "r", "core_tension": "t"}
THEME_B = {"action": "NEEDS_APPROVAL_THEME", "proposed_theme": "theme-b", "reason": "r", "core_tension": "t"}
ALREADY = {"action": "ALREADY_PROCESSED", "reason": "entry exists for this video"}


def _proceed(slug: str, *themes: str) -> Dict[str, Any]:
    return {"action": "PROCEED", "entry_slug": slug, "entry_body": f"body of {slug}\n", "timeline_row": f"| {slug} |",
            "themes_attached": list(themes),
            "theme_updates": [{"slug": t, "new_body": f"# {t}\n"} for t in themes]}


def _reply(text: str) -> Dict[str, Any]:
    return {"text": f"---kb-plan---\n{yaml.safe_dump(json.loads(text))}\n---kb-plan---", "duration_s": 1.0,
            "input_tokens": 100, "output_tokens": 10, "cache_read_tokens": 0, "cache_creation_tokens": 0,
            "cost_usd": 0.01, "model": "stub", "stop_reason": "stop"}


class Rig:
    """One run's world. `llm` answers from the replies in the user message and the call count, as the real one might."""

    def __init__(self, tmp_path: Path):
        self.tmp_path = tmp_path
        self.processed = tmp_path / "processed"
        self.vault = tmp_path / "vault" / "gonzalo-book"
        self.llm: Callable[[List[str], int], Dict[str, Any]] = lambda replies, n: _proceed("slug")
        self.llm_calls: List[List[str]] = []
        self.replies: List[str] = []
        self.prompts: List[Dict[str, Any]] = []
        self.ran: List[str] = []

    @property
    def run_dir(self) -> Path:
        return self.processed / RID / "langgraph"

    def record(self) -> Dict[str, Any]:
        return json.loads((self.run_dir / "run.json").read_text())

    def entries(self) -> List[str]:
        return sorted(p.stem for p in (self.vault / "entries").glob("*.md"))

    def metric(self) -> Dict[str, Any]:
        rows = [json.loads(line) for line in (self.run_dir / "runs.jsonl").read_text().splitlines()]
        return next(r for r in rows if r["stage"] == "kb-curator")

    def args(self, **kw: Any) -> argparse.Namespace:
        transcript = self.tmp_path / "2026-11-05-t.txt"
        transcript.write_text("words")
        base = dict(processed_root=self.processed, run_suffix="langgraph", from_transcript=str(transcript), profile="sandbox",
                    auto_approve=False, telegram_on_error=False, reminder_interval=600, target_seconds=600, dry_run=False,
                    no_archive=True)
        return argparse.Namespace(**{**base, **kw})

    def start(self, **kw: Any) -> int:
        return run._process_one_video(self.args(**kw), RID)

    def retry(self, **kw: Any) -> int:
        graph, saver = graph_mod.build_graph(start_at="extract")
        try:
            return retry._retry_one(graph, RID, video=None, args=self.args(**kw))
        finally:
            saver.conn.close()


@pytest.fixture
def rig(tmp_path, monkeypatch) -> Rig:
    r = Rig(tmp_path)
    monkeypatch.setattr(graph_mod, "CHECKPOINT_DB", tmp_path / "checkpoints.db")
    monkeypatch.setattr(kb_curator, "VAULT_ROOT", r.vault)
    monkeypatch.setattr(run, "telegram_send", lambda *a, **k: 0)
    monkeypatch.setattr(retry, "telegram_send", lambda *a, **k: 0)

    def llm(model, system, user, max_tokens=0):
        replies = [line.split("Gonzalo's reply: ", 1)[1] for line in user.splitlines() if "Gonzalo's reply: " in line]
        r.llm_calls.append(replies)
        out = r.llm(replies, len(r.llm_calls))
        if isinstance(out, Exception):
            raise out
        return _reply(json.dumps(out))

    monkeypatch.setattr(kb_curator, "call_llm", llm)

    def stub(name: str, **out: Any):
        def node(state):
            r.ran.append(name)
            return out

        return node

    for name, out in {
        "extract": {"extraction_report": "report", "video_date": "2026-11-05"},
        "extract_image": {},
        "pre_validator": {},
        "writer": {"blog_post_title": "t"},
        "research": {},
        "notion_blog": {},
        "notion_research": {},
        "youtube_metadata": {},
        "youtube_upload": {},
        "wordpress_draft": {},
        "bv_research": {},
        "bv_wordpress": {},
        "bv_youtube": {},
        "summarizer": {"validator_verdict": "PASS"},
    }.items():
        monkeypatch.setattr(graph_mod, f"node_{name}", stub(name, **out))

    # The real mailbox and no Telegram: a thread plays the user, answering each pending.json in turn.
    real_ask = hitl.ask

    def ask(run_dir, request, interval, max_reminders=None):
        stop = threading.Event()

        def answer():
            while not stop.is_set():
                pending = hitl.read_pending(run_dir)
                if pending:
                    r.prompts.append(pending)
                    hitl.write_reply(run_dir, pending["id"], r.replies.pop(0) if r.replies else "abort")
                    return
                time.sleep(0.005)

        t = threading.Thread(target=answer)
        t.start()
        try:
            return real_ask(run_dir, request, interval, max_reminders, telegram=False, poll_s=0.01)
        finally:
            stop.set()
            t.join()

    monkeypatch.setattr(hitl, "ask", ask)
    return r


# --- defect: a second interrupt in the same node read as "nothing pending" ----------------------------------


def _refine(replies: List[str], n: int) -> Dict[str, Any]:
    if not replies:
        return THEME_A
    if replies == ["call it theme-b"]:
        return THEME_B
    return _proceed("entry-b", "theme-b")


def test_a_free_text_answer_asks_again_and_the_run_goes_on(rig):  # [DT-A3]
    rig.llm = _refine
    rig.replies = ["call it theme-b", "yes"]
    assert rig.start() == 0
    assert [("theme-a" in p["prompt"], "theme-b" in p["prompt"]) for p in rig.prompts] == [(True, False), (False, True)]
    assert "writer" in rig.ran and "summarizer" in rig.ran
    assert rig.entries() == ["entry-b"]
    rec = rig.record()
    assert rec["status"] == "done" and rec["verdict"] == "PASS" and rec["error"] is None


def test_yes_on_a_re_emitted_unknown_action_asks_again(rig):  # [DT-A5]
    rig.llm = lambda replies, n: _proceed("entry-c") if "skip it" in replies else ALREADY
    rig.replies = ["yes", "skip it"]
    assert rig.start() == 0
    assert len(rig.prompts) == 2 and all("ALREADY_PROCESSED" in p["prompt"] for p in rig.prompts)
    assert "writer" in rig.ran and rig.entries() == ["entry-c"]
    assert rig.record()["verdict"] == "PASS"


def test_a_second_theme_under_auto_approve_is_answered_too(rig):  # [DT-A1]
    def llm(replies, n):
        return [THEME_A, THEME_B, _proceed("entry-ab", "theme-a", "theme-b")][min(len(replies), 2)]

    rig.llm = llm
    assert rig.start(auto_approve=True) == 0
    assert "writer" in rig.ran and rig.entries() == ["entry-ab"]
    assert rig.record()["verdict"] == "PASS"


def test_a_run_that_stops_with_pending_work_is_never_recorded_done(rig, monkeypatch):  # [DT-F1]
    rig.llm = lambda replies, n: THEME_A
    monkeypatch.setattr(run, "_get_pending_interrupt", lambda graph, config: None)
    with pytest.raises(RuntimeError, match="kb_curator"):
        rig.start()
    rec = rig.record()
    assert rec["status"] == "failed" and rec["verdict"] is None and "kb_curator" in rec["error"]
    assert "writer" not in rig.ran


def test_a_retry_that_stops_with_pending_work_is_never_recorded_done(rig, monkeypatch):  # [DT-F1]
    rig.llm = lambda replies, n: THEME_A
    monkeypatch.setattr(run, "_get_pending_interrupt", lambda graph, config: None)
    with pytest.raises(RuntimeError):
        rig.start()
    monkeypatch.setattr(retry, "_get_pending_interrupt", lambda graph, config: None)
    assert rig.retry() == 2
    assert rig.record()["status"] == "failed" and "kb_curator" in rig.record()["error"]
