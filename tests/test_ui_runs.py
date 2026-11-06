"""pipeline/ui/runs.py, artifacts.py and mapping.py over the C6 fixtures."""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest

from pipeline.ui import artifacts, mapping, runs

FIXTURES = Path(__file__).parent / "fixtures" / "ui" / "processed"
NODES = [
    "transcribe", "extract", "kb_curator", "writer", "research", "notion_blog",
    "notion_research", "extract_image", "youtube_metadata", "youtube_upload", "wordpress_draft",
    "pre_validator", "bv_research", "bv_wordpress", "bv_youtube", "summarizer",
]


@pytest.fixture
def root(tmp_path: Path) -> Path:
    dst = tmp_path / "processed"
    shutil.copytree(FIXTURES, dst)
    return dst


def _set_pid(root: Path, run_id: str, pid: int) -> None:
    path = root / run_id / "langgraph" / "run.json"
    data = json.loads(path.read_text())
    data["pid"] = pid
    path.write_text(json.dumps(data))


def _ev(node, event, ts, **kw):
    return {"ts": ts, "node": node, "event": event, "attempt": kw.pop("attempt", 1), **kw}


# ---------- mapping ----------

def test_every_graph_node_is_in_all_three_maps(tmp_path, monkeypatch):
    monkeypatch.setenv("CHECKPOINT_DB_PATH", str(tmp_path / "ck.db"))
    from pipeline import graph

    monkeypatch.setattr(graph, "CHECKPOINT_DB", tmp_path / "ck.db")
    compiled, saver = graph.build_graph()
    saver.conn.close()
    nodes = set(compiled.get_graph().nodes) - {"__start__", "__end__"}
    assert nodes == set(NODES)
    for m in (mapping.NODE_TO_STAGE, mapping.NODE_TO_DIR, mapping.NODE_ICON):
        assert nodes <= set(m)


def test_stage_lookup_handles_legacy_alias():
    assert mapping.node_for_stage("extract-image") == "extract_image"
    assert mapping.node_for_stage("validator") == "summarizer"
    assert mapping.node_for_stage("summarizer") == "summarizer"
    assert mapping.node_for_stage("nope") is None


# ---------- pid / status ----------

def test_pid_le_zero_is_dead_without_os_kill(monkeypatch):
    def boom(*a):
        raise AssertionError("os.kill must not be called")

    monkeypatch.setattr(os, "kill", boom)
    assert runs.pid_alive(0) is False
    assert runs.pid_alive(-1) is False
    assert runs.pid_alive(None) is False


def test_pid_alive_checks_real_pids():
    assert runs.pid_alive(os.getpid()) is True
    assert runs.pid_alive(99999999) is False


def test_permission_error_means_alive(monkeypatch):
    def denied(pid, sig):
        raise PermissionError

    monkeypatch.setattr(os, "kill", denied)
    assert runs.pid_alive(1) is True


@pytest.mark.parametrize("run_id,status", [
    ("fx-done", "done"), ("fx-running", "crashed"), ("fx-hitl", "crashed"),
    ("fx-failed", "failed"), ("fx-crashed", "crashed"), ("fx-legacy", "done"),
])
def test_fixture_status(root, run_id, status):
    assert runs.load_run(root, run_id, NODES)["status"] == status


def test_live_pid_keeps_active_status(root):
    _set_pid(root, "fx-running", os.getpid())
    _set_pid(root, "fx-hitl", os.getpid())
    assert runs.load_run(root, "fx-running", NODES)["status"] == "running"
    assert runs.load_run(root, "fx-hitl", NODES)["status"] == "waiting_hitl"


# ---------- node states ----------

def test_last_event_wins_and_never_started_is_pending():
    evs = [_ev("a", "start", 1), _ev("a", "error", 2, error="X: y"),
           _ev("a", "start", 3, attempt=2), _ev("b", "start", 1)]
    st = runs.node_states(evs, ["a", "b", "c"], finished=False, input_kind="video")
    assert st["a"]["state"] == "running" and st["a"]["attempts"] == 2 and st["a"]["error"] is None
    assert st["b"]["state"] == "running"
    assert st["c"] == {"state": "pending", "started_at": None, "ended_at": None,
                       "duration_s": None, "attempts": 0, "error": None,
                       "result": {}, "outputs": {}, "facts": {}, "skip_reason": None}


def test_error_state_carries_message():  # [DT-R2]
    evs = [_ev("a", "start", 1), _ev("a", "error", 2, error="X: y")]
    st = runs.node_states(evs, ["a"], finished=True, input_kind="video")["a"]
    assert st["state"] == "error" and st["error"] == "X: y" and st["ended_at"] == 2


def test_noop_refire_never_moves_done_back_to_running():
    evs = [_ev("s", "start", 1), _ev("s", "end", 3, duration_s=2.0, noop=False, outputs={}),
           _ev("s", "start", 4), ]
    assert runs.node_states(evs, ["s"], False, "video")["s"]["state"] == "done"
    evs.append(_ev("s", "end", 4.1, duration_s=0.1, noop=True, outputs={}))
    st = runs.node_states(evs, ["s"], False, "video")["s"]
    assert st["state"] == "done" and st["duration_s"] == 2.0 and st["ended_at"] == 3


def test_noop_only_node_is_done():
    evs = [_ev("s", "start", 1), _ev("s", "end", 1.1, duration_s=0.1, noop=True, outputs={})]
    st = runs.node_states(evs, ["s"], False, "video")["s"]
    assert st["state"] == "done" and st["duration_s"] == 0.1


def test_hitl_sequence():
    evs = [_ev("k", "start", 1), _ev("k", "paused", 2)]
    assert runs.node_states(evs, ["k"], False, "video")["k"]["state"] == "paused"
    evs += [_ev("k", "start", 10, attempt=2), _ev("k", "end", 15, duration_s=5.0, noop=False, outputs={})]
    st = runs.node_states(evs, ["k"], False, "video")["k"]
    assert st["state"] == "done" and st["started_at"] == 1 and st["attempts"] == 2


def test_transcribe_skipped_only_when_finished_transcript_run():
    assert runs.node_states([], ["transcribe"], True, "transcript")["transcribe"]["state"] == "skipped"
    assert runs.node_states([], ["transcribe"], False, "transcript")["transcribe"]["state"] == "pending"
    assert runs.node_states([], ["transcribe"], True, "video")["transcribe"]["state"] == "pending"


def test_fx_done_nodes(root):
    run = runs.load_run(root, "fx-done", NODES)
    states = {n: v["state"] for n, v in run["nodes"].items()}
    assert states.pop("transcribe") == "skipped"
    assert states.pop("youtube_upload") == "skipped"
    assert states.pop("wordpress_draft") == "skipped"
    assert set(states.values()) == {"done"}
    assert run["nodes"]["youtube_upload"]["skip_reason"] == "off"
    assert run["nodes"]["wordpress_draft"]["skip_reason"] == "manual"
    assert run["nodes"]["transcribe"]["skip_reason"] is None
    assert run["nodes"]["kb_curator"]["attempts"] == 2
    assert run["nodes"]["summarizer"]["duration_s"] == 1.1


def test_fx_running_and_failed_nodes(root):
    run = runs.load_run(root, "fx-running", NODES)
    assert run["nodes"]["transcribe"]["state"] == "done"
    assert run["nodes"]["extract"]["state"] == "running"
    assert run["nodes"]["writer"]["state"] == "pending"
    failed = runs.load_run(root, "fx-failed", NODES)
    assert failed["nodes"]["research"]["state"] == "error"
    assert failed["nodes"]["research"]["error"].startswith("RuntimeError:")
    assert failed["nodes"]["notion_research"]["state"] == "pending"
    assert failed["error"].startswith("RuntimeError:")


def test_fx_hitl_paused(root):
    run = runs.load_run(root, "fx-hitl", NODES)
    assert run["nodes"]["kb_curator"]["state"] == "paused"


# ---------- telemetry ----------

def test_outputs_are_absolute_and_from_the_last_real_end(root):
    rd = (root / "fx-done" / "langgraph").resolve()
    nodes = runs.load_run(root, "fx-done", NODES)["nodes"]
    assert nodes["kb_curator"]["outputs"] == {
        "vault_entry_path": str(rd.parents[2] / "vault" / "entries" / "2026-10-30.md"),
        "curator_summary_path": str(rd / "kb-curator" / "curator_summary.md"),
    }
    assert nodes["summarizer"]["outputs"] == {}  # the noop fires leave it empty, like `result`
    assert runs.load_run(root, "fx-legacy", NODES)["nodes"]["writer"]["outputs"] == {}


def test_fx_done_telemetry(root):
    run = runs.load_run(root, "fx-done", NODES)
    t = run["telemetry"]
    assert t["total_s"] == pytest.approx(298.0)
    assert t["work_s"] == pytest.approx(178.0)
    kb = t["per_node"]["kb_curator"]
    assert (kb["input_tokens"], kb["output_tokens"]) == (15100, 3950)
    assert (kb["cache_read_tokens"], kb["cache_creation_tokens"]) == (4500, 4500)
    assert kb["cost_usd"] == pytest.approx(0.31)
    assert t["per_node"]["extract"]["model"] == "claude-sonnet-5-5"
    assert t["per_node"]["extract_image"]["input_tokens"] == 0
    assert t["tokens"] == {"input": 2200 + 15100 + 4260 + 1200 + 51000, "output": 440 + 3950 + 657 + 100 + 3300,
                           "cache_read": 4500 + 38000, "cache_creation": 2081 + 4500 + 3058 + 12500}
    assert t["cost_usd"] == pytest.approx(0.012 + 0.31 + 0.025 + 0.005 + 0.4)
    assert "pre_validator" not in t["per_node"]


def test_rows_for_one_node_sum():
    rows = [{"ts": 1.0, "stage": "validator", "duration_s": 1.0, "input_tokens": 3},
            {"ts": 2.0, "stage": "summarizer", "duration_s": 0.5, "cost_usd": 0.1},
            {"ts": 3.0, "stage": "unknown-stage", "duration_s": 9.0}]
    t = runs.telemetry(rows, total_s=None, hitl_wait_s=0.0)
    assert t["per_node"] == {"summarizer": {
        "duration_s": 1.5, "input_tokens": 3, "output_tokens": 0, "cache_read_tokens": 0,
        "cache_creation_tokens": 0, "cost_usd": 0.1, "cost_estimated": False, "model": None, "calls": 1, "in_flight": 0,
        "live": False}}
    assert t["work_s"] is None


def _start(node, call_id):
    return {"ts": 1.0, "event": "start", "call_id": call_id, "node": node, "attempt": 1, "model": "m"}


def _end(node, call_id, tokens):
    return {**_start(node, call_id), "event": "end", "input_tokens": tokens, "output_tokens": 1,
            "cache_read_tokens": 0, "cache_creation_tokens": 0, "cost_usd": 0.5, "call_s": 2.0}


def test_c4_is_final_over_c8_no_double_count():
    rows = [{"stage": "extract", "duration_s": 3.0, "model": "c4", "input_tokens": 100, "cost_usd": 0.1}]
    usage = [_start("extract", "a"), _end("extract", "a", 7), _start("extract", "b"), _end("extract", "b", 7)]
    t = runs.telemetry(rows, None, 0.0, usage, active=True)
    ex = t["per_node"]["extract"]
    assert (ex["input_tokens"], ex["cost_usd"], ex["model"]) == (100, 0.1, "c4")
    assert (ex["calls"], ex["in_flight"], ex["live"]) == (2, 0, False)
    assert t["tokens"]["input"] == 100 and t["cost_usd"] == 0.1 and t["calls"] == 2


def test_c8_only_node_is_live_and_counts_in_flight():
    usage = [_start("research", "a"), _end("research", "a", 5), _start("research", "e"),
             {**_start("research", "e"), "event": "error", "error": "boom"}, _start("research", "open")]
    t = runs.telemetry([], None, 0.0, usage, active=True)
    res = t["per_node"]["research"]
    assert res["duration_s"] is None and res["live"] is True and res["model"] == "m"
    assert (res["input_tokens"], res["output_tokens"], res["cost_usd"]) == (5, 1, 0.5)
    assert (res["calls"], res["in_flight"]) == (1, 1)
    assert (t["calls"], t["in_flight"], t["tokens"]["input"]) == (1, 1, 5)
    assert runs.telemetry([], None, 0.0, usage, active=False)["in_flight"] == 0


def test_c4_row_without_tokens_does_not_finalise_c8():
    rows = [{"stage": "youtube-upload", "duration_s": 1.0, "skipped": True}]
    usage = [_start("youtube_upload", "a"), _end("youtube_upload", "a", 9)]
    yt = runs.telemetry(rows, None, 0.0, usage)["per_node"]["youtube_upload"]
    assert (yt["input_tokens"], yt["calls"], yt["live"], yt["duration_s"]) == (9, 1, True, 1.0)


def test_calls_from_c4_only(root):
    per_node = runs.load_run(root, "fx-done", NODES)["telemetry"]["per_node"]
    assert per_node["kb_curator"]["calls"] == 2  # llm_calls
    assert per_node["extract"]["calls"] == 1  # tokens, no llm_calls
    assert per_node["extract_image"]["calls"] == 0  # no tokens
    assert per_node["extract"]["live"] is False


def test_fx_running_in_flight_only_while_live(root):
    crashed = runs.load_run(root, "fx-running", NODES)["telemetry"]
    assert crashed["in_flight"] == 0 and crashed["per_node"]["extract"]["input_tokens"] == 1200
    _set_pid(root, "fx-running", os.getpid())
    live = runs.load_run(root, "fx-running", NODES)["telemetry"]
    ex = live["per_node"]["extract"]
    assert (ex["calls"], ex["in_flight"], ex["live"], ex["input_tokens"]) == (1, 1, True, 1200)
    assert (live["calls"], live["in_flight"]) == (1, 1)


def test_active_run_total_uses_now(root):
    _set_pid(root, "fx-running", os.getpid())
    run = runs.load_run(root, "fx-running", NODES, now=runs.load_run(root, "fx-running", NODES)["started_at"] + 50)
    assert run["telemetry"]["total_s"] == pytest.approx(50)


def test_crashed_run_total_ends_at_last_event(root):
    run = runs.load_run(root, "fx-crashed", NODES)
    assert run["telemetry"]["total_s"] == pytest.approx(13.4)


# ---------- legacy ----------

def test_legacy_run(root):
    run = runs.load_run(root, "fx-legacy", NODES)
    assert run["status"] == "done" and run["verdict"] == "PASS"
    assert run["profile"] is None and run["hitl_wait_s"] == 0.0
    assert run["input"]["kind"] == "video"
    assert run["input"]["path"].endswith("PXL_20260515_201436156.mp4")
    assert run["ended_at"] - run["started_at"] == pytest.approx(649 - 37)
    assert run["nodes"]["summarizer"]["state"] == "done"
    assert run["nodes"]["summarizer"]["duration_s"] == pytest.approx(0.97 + 0.83 + 1.09 + 1.07)
    assert run["nodes"]["transcribe"]["state"] == "done"
    assert run["nodes"]["pre_validator"]["state"] == "skipped"
    assert run["telemetry"]["per_node"]["kb_curator"]["input_tokens"] == 7481 + 7732


def test_legacy_transcript_run_without_source(tmp_path):
    d = tmp_path / "old" / "langgraph"
    d.mkdir(parents=True)
    (d / "runs.jsonl").write_text(json.dumps({"ts": 5.0, "stage": "extract", "duration_s": 1.0}) + "\n"
                                  + "not json\n")
    run = runs.load_run(tmp_path, "old", NODES)
    assert run["input"] == {"kind": "transcript", "path": None}
    assert run["verdict"] is None
    assert run["nodes"]["transcribe"]["state"] == "skipped"


# ---------- listing ----------

def test_list_runs_newest_first_and_shape(root):
    (root / "no-langgraph").mkdir()
    (root / "empty" / "langgraph").mkdir(parents=True)
    (root / "a-file").write_text("x")
    listed = runs.list_runs(root)
    ids = [r["run_id"] for r in listed]
    assert set(ids) == {"fx-done", "fx-published", "fx-running", "fx-hitl", "fx-failed", "fx-crashed", "fx-legacy",
                        "fx-error", "fx-oldwait", "fx-stopped", "fx-nocheckpoint", "fx-modeoff", "fx-unpackaged"}
    starts = [r["started_at"] for r in listed]
    assert starts == sorted(starts, reverse=True)
    done = next(r for r in listed if r["run_id"] == "fx-done")
    assert done == {"run_id": "fx-done", "status": "done", "verdict": "PASS",
                    "started_at": done["started_at"], "ended_at": done["ended_at"],
                    "input": {"kind": "transcript", "name": "transcript_2026-10-30.txt"},
                    "total_s": pytest.approx(298.0), "cost_usd": done["cost_usd"], "cost_estimated": False,
                    "tokens": done["tokens"], "failed_node": None,
                    "attention": [{"kind": "publish", "label": "wordpress: package pending", "target": "wordpress"}]}


def test_list_runs_missing_root(tmp_path):
    assert runs.list_runs(tmp_path / "nope") == []


@pytest.mark.parametrize("bad", ["nope", "..", ".", "", "fx-done/../fx-done"])
def test_load_run_unknown_is_none(root, bad):
    assert runs.load_run(root, bad, NODES) is None


# ---------- artifacts ----------

def test_fx_done_artifacts_one_of_each_kind(root):  # [DT-R2] [DT-D3]
    arts = artifacts.list_artifacts(root / "fx-done" / "langgraph")
    kinds = {a["kind"] for a in arts}
    assert kinds == {"markdown", "csv", "json", "text", "image", "other"}
    by_name = {a["name"]: a for a in arts}
    assert by_name["blog_post.md"]["node"] == "writer"
    assert by_name["featured.png"]["node"] == "extract_image"
    assert by_name["frames.bin"]["node"] == "wordpress_draft"
    assert by_name["audit_report.md"]["node"] == "summarizer"
    assert by_name["research_report.csv"]["bytes"] > 0
    assert len({a["id"] for a in arts}) == len(arts)
    # outputs pointing at missing files are not listed
    assert not any(str(a["path"]).startswith("/nonexistent") for a in arts)


def test_external_outputs_listed_and_internal_deduped(tmp_path):
    rd = tmp_path / "r" / "langgraph"
    (rd / "painforwisdom-writer").mkdir(parents=True)
    blog = rd / "painforwisdom-writer" / "blog_post.md"
    blog.write_text("# b")
    (rd / "stray-dir").mkdir()
    (rd / "stray-dir" / "x.log").write_text("l")
    ext = tmp_path / "transcript.txt"
    ext.write_text("t")
    (rd / "events.jsonl").write_text("\n".join(json.dumps(e) for e in [
        _ev("transcribe", "end", 1, duration_s=1, noop=False, outputs={"transcript_path": str(ext)}),
        _ev("writer", "end", 2, duration_s=1, noop=False, outputs={"blog_post_path": str(blog)}),
    ]) + "\n")
    arts = artifacts.list_artifacts(rd)
    assert [(a["node"], a["name"], a["kind"]) for a in arts] == [
        ("transcribe", "transcript.txt", "text"),
        ("writer", "blog_post.md", "markdown"),
        (None, "x.log", "text"),
    ]


def test_artifact_ids_are_stable_and_opaque(root):
    a1 = artifacts.list_artifacts(root / "fx-done" / "langgraph")
    a2 = artifacts.list_artifacts(root / "fx-done" / "langgraph")
    assert [a["id"] for a in a1] == [a["id"] for a in a2]
    assert all("/" not in a["id"] and "." not in a["id"] for a in a1)
    assert artifacts.find_artifact(root / "fx-done" / "langgraph", a1[0]["id"]) == a1[0]
    assert artifacts.find_artifact(root / "fx-done" / "langgraph", "../run.json") is None


def test_skipped_from_last_row_survives_noop_refire_and_never_hides_live_state():
    rows = [{"stage": "youtube-upload", "skipped": True, "reason": "off", "ts": "t", "cost_usd": 0.1,
             "input_tokens": 1, "duration_s": 2.0}]
    done = [_ev("youtube_upload", "start", 1), _ev("youtube_upload", "end", 2, duration_s=1, noop=False, outputs={})]

    def state(evs, r=rows):
        return runs.apply_rows(runs.node_states(evs, ["youtube_upload"], False, "video"), r)["youtube_upload"]

    assert state(done[:1])["state"] == "running"
    n = state(done)
    assert n["state"] == "skipped" and n["skip_reason"] == "off"
    assert n["facts"] == {"skipped": True, "reason": "off", "duration_s": 2.0}
    assert state(done + [_ev("youtube_upload", "start", 3)])["state"] == "skipped"
    assert state([])["state"] == "skipped"
    assert state(done, rows + [{"stage": "youtube-upload", "video_id": "v"}])["state"] == "done"  # last row decides


def test_result_is_last_non_noop_and_old_events_read_empty():
    evs = [_ev("s", "end", 1, duration_s=1, noop=False, outputs={}, result={"a": 1}),
           _ev("s", "end", 2, duration_s=1, noop=True, outputs={}, result={})]
    assert runs.node_states(evs, ["s"], False, "video")["s"]["result"] == {"a": 1}
    old = [_ev("s", "end", 1, duration_s=1, noop=False, outputs={})]
    assert runs.node_states(old, ["s"], False, "video")["s"]["result"] == {}


def test_legacy_run_skipped_from_rows(root):
    run = runs.load_run(root, "fx-legacy", NODES)
    assert run["nodes"]["youtube_upload"]["state"] == "skipped"
    assert run["nodes"]["youtube_upload"]["result"] == {}
    assert "input_tokens" not in run["nodes"]["writer"]["facts"] and run["nodes"]["writer"]["facts"]


def test_output_inside_run_dir_owns_the_file_over_its_dir(tmp_path):
    rd = tmp_path / "r" / "langgraph"
    (rd / "wordpress-draft").mkdir(parents=True)
    img = rd / "wordpress-draft" / "featured.jpg"
    img.write_bytes(b"x")
    (rd / "wordpress-draft" / "post.md").write_text("p")
    (rd / "events.jsonl").write_text(json.dumps(
        _ev("extract_image", "end", 1, duration_s=1, noop=False, outputs={"featured_image_path": str(img)})) + "\n")
    assert {a["name"]: a["node"] for a in artifacts.list_artifacts(rd)} == {
        "featured.jpg": "extract_image", "post.md": "wordpress_draft"}
