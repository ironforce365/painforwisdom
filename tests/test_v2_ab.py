"""A/B harness (spec 2026-11-09-session-pipeline-v2 §7), with subprocess faked: no engine runs."""
import json
import subprocess
from pathlib import Path

from pipeline.v2 import ab


def _finding(name, ok, severity="core", detail="x"):
    return json.dumps({"name": name, "ok": ok, "detail": detail, "severity": severity})


def _fake_run(run_dir: Path, engine: str) -> None:
    """A run folder in the console's formats: run.json, events.jsonl, usage.jsonl and the files they point at."""
    run_dir.mkdir(parents=True)
    (run_dir / "blog.md").write_text("one two three four")
    (run_dir / "entry.md").write_text("# e\n**Themes:** [[grit]], [[patience]]\n")
    (run_dir / "meta.json").write_text(json.dumps({"title": f"yt {engine}"}))
    refused = engine == "v2"
    (run_dir / "run.json").write_text(json.dumps({
        "status": "failed" if refused else "done", "verdict": None if refused else "PASS",
        "started_at": 100.0, "ended_at": 160.0, "hitl_wait_s": 0.0,
        "error": "RuntimeError: kb-curator needs a decision, --auto-approve cannot give one: dup" if refused else None}))
    events = [
        {"node": "writer", "event": "start", "attempt": 1},
        {"node": "writer", "event": "error", "attempt": 1, "error": "TimeoutError: t"},
        {"node": "writer", "event": "start", "attempt": 2},
        {"node": "writer", "event": "end", "attempt": 2, "duration_s": 4.0,
         "outputs": {"blog_post_path": str(run_dir / "blog.md")}, "result": {"blog_post_title": "Title"}},
        {"node": "kb_curator", "event": "end", "attempt": 1, "duration_s": 2.0,
         "outputs": {"vault_entry_path": str(run_dir / "entry.md")}, "result": {}},
        {"node": "youtube_metadata", "event": "end", "attempt": 1, "duration_s": 1.0,
         "outputs": {"youtube_metadata_path": str(run_dir / "meta.json")}, "result": {}},
        {"node": "research", "event": "end", "attempt": 1, "duration_s": 3.0, "outputs": {},
         "result": {"research_count": 5}},
        {"node": "summarizer", "event": "start", "attempt": 1},  # v1 join fires twice: not a retry
        {"node": "summarizer", "event": "start", "attempt": 2},
        {"node": "pre_validator", "event": "end", "attempt": 1, "duration_s": 0.1, "outputs": {},
         "result": {"pre_findings": [_finding("same", True, detail=engine), _finding("flips", engine == "v1")]}},
    ]
    (run_dir / "events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))
    usage = [{"event": "end", "input_tokens": 100, "output_tokens": 10, "cache_read_tokens": 40,
              "cache_creation_tokens": 20}, {"event": "error", "error": "x"}]
    (run_dir / "usage.jsonl").write_text("".join(json.dumps(u) + "\n" for u in usage))


def test_ab_runs_engines_one_after_the_other_and_reports(tmp_path, monkeypatch):
    transcript = tmp_path / "t.txt"
    transcript.write_text("hello")
    calls = []

    def fake_run(cmd, cwd, env, stdout, stderr):
        engine = "v2" if cmd[2] == "pipeline.v2.run" else "v1"
        run_id = cmd[cmd.index("--run-id") + 1]
        calls.append((engine, run_id, env.get("ANTHROPIC_AUTH_TOKEN")))
        assert "--dry-run" in cmd and "--auto-approve" in cmd
        _fake_run(tmp_path / "processed" / run_id / "langgraph", engine)
        return subprocess.CompletedProcess(cmd, 0 if engine == "v1" else 2)

    monkeypatch.setattr(ab.subprocess, "run", fake_run)
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "tok")
    assert ab.main(["--from-transcript", str(transcript), "--repeat", "2",
                    "--processed-root", str(tmp_path / "processed")]) == 0

    assert [(e, r.rsplit("-", 2)[1:]) for e, r, _ in calls] == [
        ("v1", ["1", "v1"]), ("v2", ["1", "v2"]), ("v1", ["2", "v1"]), ("v2", ["2", "v2"])]
    assert [t for _, _, t in calls] == ["tok", "", "tok", ""]  # bl-dff0b22f workaround, v2 child only

    out = next((tmp_path / "processed" / "ab").iterdir())
    report = json.loads((out / "report.json").read_text())
    v1, v2 = report["pairs"][0]["v1"], report["pairs"][0]["v2"]
    assert v1["verdict"] == "PASS" and not v1["refused"]
    assert v2["refused"] and v2["rc"] == 2
    assert v1["wall_s"] == 60.0 and v1["retries"] == 1 and v1["errors"] == ["writer#1: TimeoutError: t"]
    assert v1["nodes"]["writer"] == {"duration_s": 4.0, "attempts": 2}
    assert v1["llm_calls"] == 1 and v1["llm_errors"] == 1
    assert v1["tokens"]["prompt_total"] == 100 and v2["tokens"]["prompt_total"] == 160
    assert v1["outputs"] == {"blog_title": "Title", "blog_word_count": 4, "research_rows": 5,
                             "themes_attached": ["grit", "patience"], "youtube_title": "yt v1"}
    assert [d["finding"] for d in report["pairs"][0]["findings_diff"]] == ["pre_findings: flips"]
    md = (out / "report.md").read_text()
    assert "| refused by --auto-approve | False | True |" in md and "## Pair 2" in md


def test_collect_tolerates_an_empty_run_folder(tmp_path):
    row = ab.collect("v2", tmp_path)
    assert row["verdict"] is None and row["llm_calls"] == 0 and row["outputs"]["themes_attached"] == []
