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


def test_auto_approve_gives_up_on_endless_proposals(rig):  # [DT-A1]
    # Each `yes` buys another LLM call: unattended, an endless proposer must fail instead of spending forever.
    def llm(replies, n):
        assert n < 30, "still asking the LLM"
        return THEME_A

    rig.llm = llm
    with pytest.raises(RuntimeError, match="gave up"):
        rig.start(auto_approve=True)
    record = rig.record()
    assert record["status"] == "failed" and "auto-approve" in record["error"]
    assert len(rig.llm_calls) <= 11


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


# --- defect: retry saw `next == ()` on a task holding an error, and called the run terminal --------------------


def test_retry_after_an_answered_approval_then_a_failure_resumes(rig):  # [DT-F1]
    """The answered approval's write makes `next` empty while the failed task is still there to run."""
    boom = {"on": True}

    def llm(replies, n):
        if not replies:
            return THEME_A
        if boom["on"]:
            return RuntimeError("the LLM fell over")
        return _proceed("entry-a", "theme-a")

    rig.llm = llm
    rig.replies = ["yes", "abort"]
    with pytest.raises(RuntimeError, match="fell over"):
        rig.start()
    rec = rig.record()
    assert rec["status"] == "failed" and "fell over" in rec["error"] and "writer" not in rig.ran

    boom["on"] = False
    assert rig.retry() == 0
    assert rig.llm_calls[-1] == ["yes"], "the reply given before the failure is kept"
    assert "writer" in rig.ran and rig.entries() == ["entry-a"]
    rec = rig.record()
    assert rec["status"] == "done" and rec["verdict"] == "PASS" and rec["error"] is None


def test_retry_of_a_failed_run_with_no_new_answer_asks_nothing_again(rig):  # [DT-E1]
    boom = {"on": True}

    def llm(replies, n):
        if not replies:
            return THEME_A
        return RuntimeError("boom") if boom["on"] else _proceed("entry-a", "theme-a")

    rig.llm = llm
    rig.replies = ["yes", "abort"]
    with pytest.raises(RuntimeError):
        rig.start()
    asked = len(rig.prompts)
    boom["on"] = False
    assert rig.retry() == 0
    assert len(rig.prompts) == asked


def test_retry_of_a_run_stopped_at_its_second_question_asks_it_again(rig, monkeypatch):  # [DT-F3]
    rig.llm = _refine
    rig.replies = ["call it theme-b"]
    answered = hitl.ask

    def ask(run_dir, request, interval, max_reminders=None):
        if "theme-b" in request["prompt"]:
            raise SystemExit(143)  # what hitl.ask turns a SIGTERM into
        return answered(run_dir, request, interval, max_reminders)

    monkeypatch.setattr(hitl, "ask", ask)
    with pytest.raises(SystemExit):
        rig.start()
    assert rig.record()["status"] == "failed"

    monkeypatch.setattr(hitl, "ask", answered)
    rig.replies = ["yes"]
    assert rig.retry() == 0
    assert "theme-b" in rig.prompts[-1]["prompt"]
    assert "writer" in rig.ran and rig.entries() == ["entry-b"]
    assert rig.record()["status"] == "done"


def test_a_kept_answer_does_not_answer_a_new_question(rig):  # [DT-F1]
    """The answer kept across the failure is for the question it answered: the next one still reaches the user."""
    boom = {"on": True}

    def llm(replies, n):
        if not replies:
            return THEME_A
        if boom["on"]:
            return RuntimeError("boom")
        return THEME_B if replies == ["yes"] else _proceed("entry-b", "theme-b")

    rig.llm = llm
    rig.replies = ["yes", "abort"]
    with pytest.raises(RuntimeError):
        rig.start()
    boom["on"] = False
    rig.replies = ["go"]
    assert rig.retry() == 0
    assert "theme-b" in rig.prompts[-1]["prompt"]
    assert rig.llm_calls[-1] == ["yes", "go"]
    assert rig.entries() == ["entry-b"]


def test_retry_of_a_finished_thread_still_says_terminal(rig):  # [DT-D1]
    assert rig.start() == 0
    ran = list(rig.ran)
    assert rig.retry() == 0
    assert rig.ran == ran
    assert rig.record()["status"] == "done"


# --- defect: interrupt() replays the node from its top, and each replay asked the LLM again ---------------------


def test_a_reject_is_honoured_when_the_replayed_plan_would_be_proceed(rig):  # [DT-A2]
    """Without a memo the replay gets a fresh answer to the empty history, and this one drops the user's `no`."""

    def llm(replies, n):
        if "no" in replies:
            return _proceed("entry-existing")
        return THEME_A if n == 1 else _proceed("entry-new-theme", "theme-a")

    rig.llm = llm
    rig.replies = ["no"]
    assert rig.start() == 0
    assert rig.entries() == ["entry-existing"]
    assert rig.llm_calls == [[], ["no"]]


def test_yes_applies_to_the_proposal_the_user_saw(rig):  # [DT-A1]
    """The replay must not swap the proposal: a stored `yes` would approve a theme nobody saw."""

    def llm(replies, n):
        if replies:
            return _proceed("entry-x", "theme-a" if "theme-a" in rig.llm_prompts[-1] else "theme-b")
        return THEME_A if n == 1 else THEME_B

    rig.llm_prompts = []
    real = kb_curator.call_llm

    def spying(model, system, user, max_tokens=0):
        rig.llm_prompts.append(user)
        return real(model, system, user, max_tokens=max_tokens)

    kb_curator.call_llm = spying  # the fixture's monkeypatch restores the original at teardown
    rig.llm = llm
    rig.replies = ["yes"]
    assert rig.start() == 0
    assert "theme-a" in rig.prompts[0]["prompt"]
    assert "- Proposal:" in rig.llm_prompts[-1] and "theme-a" in rig.llm_prompts[-1]
    assert "theme-b" not in rig.llm_prompts[-1]
    assert (rig.vault / "themes" / "theme-a.md").is_file() and not (rig.vault / "themes" / "theme-b.md").exists()


def _three_rounds(replies: List[str], n: int) -> Dict[str, Any]:
    return _proceed("entry-3") if len(replies) >= 3 else [THEME_A, THEME_B, THEME_A][len(replies)]


def test_three_rounds_make_exactly_four_llm_calls_and_the_metrics_agree(rig):  # [DT-A3]
    rig.llm = _three_rounds
    rig.replies = ["first", "second", "third"]
    assert rig.start() == 0
    assert rig.llm_calls == [[], ["first"], ["first", "second"], ["first", "second", "third"]]
    metric = rig.metric()
    assert metric["llm_calls"] == 4 and metric["approvals"] == 3
    assert [c["phase"] for c in metric["per_call"]] == ["initial", "resume_1", "resume_2", "resume_3"]
    assert metric["total_cost_usd"] == pytest.approx(0.04)


def test_a_retry_reuses_the_plans_the_user_answered(rig):  # [DT-F1]
    boom = {"on": True}

    def llm(replies, n):
        if len(replies) >= 2 and boom["on"]:
            return RuntimeError("boom")
        return _three_rounds(replies + ["pad"], n) if len(replies) >= 2 else [THEME_A, THEME_B][len(replies)]

    rig.llm = llm
    rig.replies = ["first", "second", "abort"]
    with pytest.raises(RuntimeError):
        rig.start()
    assert rig.llm_calls == [[], ["first"], ["first", "second"]]
    boom["on"] = False
    assert rig.retry() == 0
    assert rig.llm_calls[3:] == [["first", "second"]], "only the plan that failed is asked for again"
    assert rig.metric()["llm_calls"] == 3


def test_a_format_retry_counts_as_two_real_calls(rig):  # [DT-A1]
    n_calls = {"n": 0}
    real = kb_curator.call_llm

    def flaky(model, system, user, max_tokens=0):
        n_calls["n"] += 1
        if n_calls["n"] == 1:
            return {**_reply(json.dumps(THEME_A)), "text": "no markers"}
        return real(model, system, user, max_tokens=max_tokens)

    kb_curator.call_llm = flaky
    rig.llm = lambda replies, n: _proceed("entry-y") if replies else THEME_A
    rig.replies = ["yes"]
    assert rig.start() == 0
    assert n_calls["n"] == 3 and rig.metric()["llm_calls"] == 3


def test_a_plan_that_fails_validation_is_not_kept_for_the_retry(rig):  # [DT-E1]
    """Nobody answered a PROCEED plan: replaying a broken one would make every retry fail the same way."""
    good = {"on": False}

    def llm(replies, n):
        return _proceed("entry-z") if good["on"] else {"action": "PROCEED", "entry_slug": "entry-z"}

    rig.llm = llm
    rig.replies = ["abort"]
    with pytest.raises(RuntimeError, match="invalid plan"):
        rig.start()
    good["on"] = True
    assert rig.retry() == 0
    assert rig.entries() == ["entry-z"] and len(rig.llm_calls) == 2
    metric = rig.metric()
    assert metric["llm_calls"] == 2, "both calls happened, the thrown-away plan's too"
    assert metric["total_cost_usd"] == pytest.approx(0.02)


def test_a_plan_with_a_yaml_date_replays_to_the_same_proposal(rig):  # [DT-A5]
    """YAML reads an unquoted date as a date, JSON stores a string: the proposal, so the memo's key, must not differ."""
    dated = {**ALREADY, "date": "2026-11-05"}
    real = kb_curator.call_llm

    def unquoted(model, system, user, max_tokens=0):
        out = real(model, system, user, max_tokens=max_tokens)
        out["text"] = out["text"].replace("'2026-11-05'", "2026-11-05")
        return out

    kb_curator.call_llm = unquoted
    rig.llm = lambda replies, n: _proceed("entry-d") if replies == ["skip"] else (dated if not replies else ALREADY)
    rig.replies = ["skip"]
    assert rig.start() == 0
    assert rig.llm_calls == [[], ["skip"]]
