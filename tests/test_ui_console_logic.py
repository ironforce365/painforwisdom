"""Pure logic behind the console's run actions, research links and prepare (spec 2026-11-05-console-end-to-end). No server."""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest

from pipeline import publishing_config
from pipeline.publish import package, specs
from pipeline.ui import actions, launcher, publish, research, runs
from pipeline.ui.settings import Settings

FIX = Path(__file__).parent / "fixtures" / "ui"
NODES = ["extract", "kb_curator", "writer", "research"]


@pytest.fixture
def root(tmp_path, monkeypatch) -> Path:
    monkeypatch.setattr(publishing_config, "CONFIG_PATH", FIX / "publishing.json")
    monkeypatch.setenv("CHECKPOINT_DB_PATH", str(tmp_path / "ck.db"))
    shutil.copytree(FIX / "processed", tmp_path / "processed")
    return tmp_path / "processed"


def _settings(root: Path, runner: str = "fake") -> Settings:
    return Settings(processed_root=root, inbox=FIX / "inbox", runner=runner, books_root=FIX / "books")


def _offer(root: Path, run_id: str, runner: str = "fake") -> dict:
    return actions.available(_settings(root, runner), runs.run_dir(root, run_id), runs.load_run(root, run_id, NODES))


def _set(root: Path, run_id: str, **fields) -> None:
    runs.update_record(root / run_id / "langgraph", **fields)


def _rec(status="failed", error=None, events=()):
    return {"status": status, "error": error, "events": list(events)}


def _ev(node, event, **kw):
    return {"node": node, "event": event, **kw}


# --- failure -----------------------------------------------------------------


def test_failure_prefers_run_error_and_names_the_node():  # [DT-F1]
    rec = _rec(error="boom", events=[_ev("a", "start"), _ev("a", "error", error="node boom")])
    assert runs.failure(rec, None)["error"] == "boom" and runs.failure(rec, None)["node"] == "a"


def test_failure_falls_back_to_the_node_error():  # [DT-F2]
    rec = _rec("crashed", events=[_ev("a", "start"), _ev("a", "error", error="node boom")])
    assert runs.failure(rec, None)["error"] == "node boom" and runs.failure(rec, None)["hint"]


def test_failure_of_a_node_that_later_succeeded_is_not_reported():  # [DT-E1] [DT-F2]
    rec = _rec("crashed", events=[_ev("a", "start"), _ev("a", "error", error="x"), _ev("a", "start"), _ev("a", "end"),
                                  _ev("b", "start")])
    got = runs.failure(rec, None)
    assert got["node"] == "b" and "died" in got["error"]


def test_old_release_approval_wait_explains_itself():  # [DT-E6]
    got = runs.failure(_rec("waiting_hitl"), None)
    assert "Telegram" in got["error"] and "stop" in got["hint"].lower()


def test_approval_wait_with_a_pending_request_is_no_failure():  # [DT-A1]
    assert runs.failure(_rec("waiting_hitl"), {"id": "x"}) is None


def test_token_total_does_not_count_cache_twice():  # [DT-D6]
    # input_tokens is litellm's prompt_tokens, which already holds both cache counts.
    assert runs.tokens_total({"input": 1000, "output": 50, "cache_read": 300, "cache_creation": 200}) == 1050


def test_failure_stopped_has_text_without_any_error():  # [DT-F3]
    assert runs.failure(_rec("stopped"), None)["error"] and runs.failure(_rec("stopped"), None)["node"] is None


def test_failure_only_for_ended_not_ok_and_old_waits():  # [DT-E6] [DT-D1]
    assert runs.failure(_rec("done"), None) is None
    assert runs.failure(_rec("running"), None) is None
    assert runs.failure(_rec("waiting_reply"), {"id": "x"}) is None
    old = runs.failure(_rec("waiting_reply"), None)
    assert "older release" in old["error"] and "stop" in old["hint"]


# --- attention ---------------------------------------------------------------


def test_needs_decision_kinds(tmp_path):  # [DT-N1]
    rec = lambda status: {"status": status}  # noqa: E731
    assert runs.needs_decision(rec("running"), tmp_path, None, None) == []
    assert runs.needs_decision(rec("waiting_hitl"), tmp_path, {"kind": "approval", "title": "T"}, None) == [
        {"kind": "approval", "label": "T"}]
    assert runs.needs_decision(rec("waiting_reply"), tmp_path, None, None)[0]["kind"] == "error"
    assert runs.needs_decision(rec("waiting_hitl"), tmp_path, None, None)[0]["kind"] == "approval"
    assert runs.needs_decision(rec("failed"), tmp_path, None, "research") == [{"kind": "failed", "label": "Failed at research"}]
    assert runs.needs_decision(rec("crashed"), tmp_path, None, None) == [{"kind": "crashed", "label": "Crashed"}]
    assert runs.needs_decision(rec("done"), tmp_path, None, None) == []


@pytest.mark.parametrize("status,listed", [("pending", True), ("failed", True), ("publishing", False), ("published", False)])
def test_publish_attention_by_package_status(tmp_path, status, listed):  # [DT-N1] [DT-P5] [DT-P6]
    package.write(tmp_path, {**package.new_package("wordpress", "manual", {}, {}), "status": status})
    got = runs.needs_decision({"status": "done"}, tmp_path, None, None)
    assert ([a["kind"] for a in got] == ["publish"]) is listed


def test_a_broken_package_file_is_not_a_crash(tmp_path):  # [DT-N1]
    (tmp_path / "wordpress-draft").mkdir()
    (tmp_path / "wordpress-draft" / "package.json").write_text("{")
    assert runs.needs_decision({"status": "done"}, tmp_path, None, None) == []


# --- actions -----------------------------------------------------------------


def test_offers_by_state(root):  # [DT-F1] [DT-F2] [DT-F3] [DT-F4] [DT-F5] [DT-F6] [DT-F7]
    assert _offer(root, "fx-stopped")["actions"] == ["retry", "restart", "dismiss"]  # F3, F4
    assert _offer(root, "fx-crashed") == {"actions": ["retry", "dismiss"], "action_blockers": {
        "restart": _offer(root, "fx-crashed")["action_blockers"]["restart"]}}  # F2, F5
    assert "checkpoint" in _offer(root, "fx-nocheckpoint")["action_blockers"]["retry"]  # F6
    assert _offer(root, "fx-unpackaged") == {"actions": [], "action_blockers": {}}  # D1, nothing to do


def test_active_offers_stop_and_blocks_the_rest(root):  # [DT-R3] [DT-S4] [DT-E6]
    _set(root, "fx-oldwait", pid=os.getpid())
    offer = _offer(root, "fx-oldwait")
    assert offer["actions"] == ["stop"] and set(offer["action_blockers"]) == {"retry", "restart"}


def test_is_runner_needs_the_module_token(monkeypatch):  # [DT-R3]
    for argv, want in (([b"python", b"-m", b"pipeline.run", b"--video", b"x"], True),
                       ([b"python", b"-m", b"pipeline.retry"], True),
                       ([b"python", b"-m", b"pipeline.ui.fake_runner"], True),
                       ([b"python", b"-m", b"pipeline.ui"], False),  # the console itself, should a pid be reused by it
                       ([b"python", b"-m", b"pipeline.publish"], False),
                       ([b"vim", b"pipeline.run.py"], False),
                       ([], False)):
        monkeypatch.setattr(actions, "_cmdline", lambda pid: argv)
        assert actions.is_runner(123) is want, argv
    assert actions.is_runner(0) is False and actions.is_runner(None) is False and actions.is_runner(-1) is False


def test_retry_argv_real_and_fake(root):  # [DT-F1]
    real = launcher.build_retry_argv(_settings(root, "real"), "r", "prod", True)
    assert real[1:8] == ["-m", "pipeline.retry", "--profile", "prod", "--run-id", "r", "--processed-root"]
    assert real[-2:] == ["--no-archive", "--auto-approve"]
    assert launcher.build_retry_argv(_settings(root, "real"), "r", None, False)[4] == "sandbox"
    assert launcher.build_retry_argv(_settings(root), "r", "prod", True)[2:4] == ["pipeline.ui.fake_runner", "retry"]


def test_input_file_resolves_relative_to_the_checkout_and_archive(root):  # [DT-F4] [DT-F5]
    rd = root / "fx-stopped" / "langgraph"
    assert runs.input_file(rd, runs._record(rd, None)).name == "transcript_2026-10-30.txt"
    gone = root / "fx-failed" / "langgraph"
    assert runs.input_file(gone, runs._record(gone, None)) is None
    legacy = root / "fx-legacy" / "langgraph"
    assert runs.input_file(legacy, runs._record(legacy, None)).parent.name == "source"


def test_log_tail_missing_is_empty(root):  # [DT-R1]
    assert actions.log_tail(_settings(root), "fx-done", 5) == {"lines": [], "bytes": 0}


# --- research ----------------------------------------------------------------


@pytest.mark.parametrize("url,kind", [
    ("https://example.org/a", "http"), ("http://example.org/a", "http"), ("file://tiny-book.txt", "local"),
    ("", "none"), ("javascript:alert(1)", "none"), ("https://", "none"), ("file:///etc/passwd", "none"),
    ("file://../publishing.json", "none"), ("file://missing.txt", "none"), ("ftp://example.org/a", "none"),
])
def test_research_link_kinds(url, kind):  # [DT-D5]
    assert research._link(url, FIX / "books", "r", 1)["kind"] == kind


def test_research_href_is_the_local_route_and_escapes():  # [DT-D5]
    assert research._link("file://tiny-book.txt", FIX / "books", "a b", 3)["href"] == "/api/runs/a%20b/research/3/file"


def test_research_media_type_is_a_short_allowlist():  # [DT-D5]
    assert research.media_type(Path("a.PDF")) == "application/pdf"
    assert research.media_type(Path("a.html")) == "application/octet-stream"
    assert research.media_type(Path("a.svg")) == "application/octet-stream"


# --- prepare and save --------------------------------------------------------


def test_prepare_state_order(root):  # [DT-P1] [DT-P2] [DT-P7]
    s = _settings(root)
    rd = root / "fx-unpackaged" / "langgraph"
    rec = runs._record(rd, None)
    assert publish._prepare_state(s, rd, rec, "wordpress", False) == {"can": True, "reason": None, "mode": "manual"}
    assert publish._prepare_state(s, rd, rec, "wordpress", True)["can"] is False
    assert publish._prepare_state(s, rd, {**rec, "status": "running"}, "youtube", False)["reason"] == "the run is still active"
    assert publish._prepare_state(s, rd, {**rec, "profile": "prod"}, "youtube", False)["mode"] == "off"
    gone = root / "fx-failed" / "langgraph"
    assert "no video" in publish._prepare_state(s, gone, runs._record(gone, None), "youtube", False)["reason"]
    assert "no blog" in publish._prepare_state(s, gone, runs._record(gone, None), "wordpress", False)["reason"]


def test_blog_source_splits_the_title(root):  # [DT-P2]
    rd = root / "fx-unpackaged" / "langgraph"
    shutil.rmtree(rd / "wordpress-draft")
    title, body = publish._blog_source(rd)
    assert title == "The road stopped arguing" and "**Title:**" not in body
    (rd / "painforwisdom-writer" / "blog_post.md").write_text("# H1 title\n\nBody\n")
    assert publish._blog_source(rd) == ("H1 title", "Body\n")
    (rd / "painforwisdom-writer" / "blog_post.md").write_text("no title here\n")
    assert publish._blog_source(rd) == ("", "no title here\n")


def test_prepared_packages_validate_for_save_and_publish(root):  # [DT-P2] [DT-P8]
    s = _settings(root)
    wp = publish.prepare(s, "fx-unpackaged", "wordpress")
    yt = publish.prepare(s, "fx-unpackaged", "youtube")
    assert package.validate_fields(wp, wp["fields"]) == [] and package.validate_fields(yt, yt["fields"]) == []
    assert wp["field_specs"] == specs.WORDPRESS and yt["field_specs"] == specs.YOUTUBE


def test_prepare_caps_long_fields_and_drops_a_bad_url(root):  # [DT-P8]
    rd = root / "fx-unpackaged" / "langgraph"
    meta = {"title": "t" * 500, "excerpt": "e" * 500, "tags": ["ok", 3], "youtube_url": "not a url"}
    (rd / "wordpress-draft" / "meta.json").write_text(json.dumps(meta))
    fields = publish.prepare(_settings(root), "fx-unpackaged", "wordpress")["fields"]
    assert len(fields["title"]) == 200 and len(fields["excerpt"]) == 300 and fields["tags"] == ["ok"]
    assert fields["youtube_url"] == ""


def test_prepare_date_only_from_a_dated_input(root):  # [DT-P2]
    assert publish._mmddyy("/x/transcript_2026-10-30.txt") == "10/30/26"
    assert publish._mmddyy("/x/PXL_20261030_070000000.mp4") == "10/30/26"
    assert publish._mmddyy("/x/undated.txt") == "" and publish._mmddyy(None) == ""


def test_save_validation_skips_required_only():  # [DT-P3] [DT-P8]
    pkg = package.new_package("wordpress", "manual", {}, specs.WORDPRESS)
    assert package.validate_fields(pkg, {"title": ""}, required=False) == []
    assert package.validate_fields(pkg, {"title": "x" * 201}, required=False) == ["title: over max 200"]
    assert "body: required" in package.validate_fields(pkg, {"title": "t"})


def test_auto_mode_package_reads_as_written_without_prepare(root):  # [DT-P10]
    rd = root / "fx-done" / "langgraph"
    pkg = package.read(rd, "wordpress")
    package.write(rd, {**pkg, "mode": "auto", "status": "published", "result": {"id": "1", "url": "https://x.invalid/1"}})
    view = publish.overview(_settings(root), rd)
    assert view["targets"]["wordpress"]["mode"] == "auto" and view["prepare"]["wordpress"]["can"] is False


def test_dead_publisher_reads_failed(root):  # [DT-P11]
    rd = root / "fx-done" / "langgraph"
    package.write(rd, {**package.read(rd, "wordpress"), "status": "publishing"})
    package.lock_path(rd, "wordpress").write_text("99999999")
    got = publish.overview(_settings(root), rd)["targets"]["wordpress"]
    assert got["status"] == "failed" and got["error"] == publish.PUBLISHER_DIED
