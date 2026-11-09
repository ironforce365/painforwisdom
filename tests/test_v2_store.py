"""pipeline.v2.store and pipeline.v2.audit (spec 2026-11-09-session-pipeline-v2 §3)."""
import hashlib
import json
import sqlite3
import threading

from pipeline.v2 import audit
from pipeline.v2.store import Store, default_path


def _rows(path, sql, *params):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn.execute(sql, params).fetchall()


def test_default_path_reads_env_at_call_time(monkeypatch, tmp_path):
    monkeypatch.setenv("PIPELINE_V2_DB", str(tmp_path / "x.db"))
    assert default_path() == tmp_path / "x.db"
    monkeypatch.delenv("PIPELINE_V2_DB")
    assert default_path().name == "v2.db"


def test_full_trail_round_trip(tmp_path, capsys):
    db = tmp_path / "v2.db"
    artifact = tmp_path / "report.md"
    artifact.write_text("hello")
    s = Store(db)
    assert _rows(db, "PRAGMA journal_mode")[0][0] == "wal"

    s.start_run("r1", profile="sandbox", dry_run=True, input_kind="transcript", input_path="t.txt")
    step = s.start_step("r1", "extract", 1, {"run_dir": 1, "transcript": 2})
    sess = s.record_session("r1", step, system_prompt="SYS", user_message="USER", model="m", allowed_tools=["Read"],
                            max_turns=1, max_tokens=100, text="OUT", messages=[{"type": "result"}],
                            input_tokens=10, output_tokens=5, num_turns=1)
    s.record_artifacts("r1", step, {"report_path": str(artifact), "missing_path": "/nope", "empty_path": "",
                                    "title": "x"})
    s.end_step(step, "ok", state_out={"title": "x"})
    ask = s.record_hitl_ask("r1", step, "approval", {"q": "ok?"})
    s.record_hitl_reply(ask, "yes")
    s.end_run("r1", "done", verdict="PASS")

    run = _rows(db, "SELECT * FROM runs")[0]
    assert (run["status"], run["verdict"], run["dry_run"]) == ("done", "PASS", 1)
    st = _rows(db, "SELECT * FROM steps")[0]
    assert st["status"] == "ok" and json.loads(st["state_out"]) == {"title": "x"}
    assert json.loads(st["state_in_keys"]) == ["run_dir", "transcript"]
    se = _rows(db, "SELECT * FROM sessions")[0]
    assert se["system_prompt"] == "SYS" and se["system_prompt_sha256"] == hashlib.sha256(b"SYS").hexdigest()
    assert json.loads(se["messages"]) == [{"type": "result"}]
    arts = {a["path"]: a for a in _rows(db, "SELECT * FROM artifacts")}
    assert set(arts) == {str(artifact), "/nope"}
    assert arts[str(artifact)]["sha256"] == hashlib.sha256(b"hello").hexdigest()
    assert arts[str(artifact)]["bytes"] == 5 and arts["/nope"]["sha256"] is None
    assert _rows(db, "SELECT reply FROM hitl")[0][0] == "yes"

    assert audit.main(["r1", "--db", str(db)]) == 0
    printed = capsys.readouterr().out
    assert "extract attempt 1  ok" in printed and "reply='yes'" in printed and str(artifact) in printed
    assert audit.main(["--session", str(sess), "--db", str(db)]) == 0
    assert "SYS" in capsys.readouterr().out
    assert audit.main(["nope", "--db", str(db)]) == 1


def test_start_run_again_resets_run(tmp_path):
    s = Store(tmp_path / "v2.db")
    s.start_run("r1")
    s.end_run("r1", "failed", error="boom")
    s.start_run("r1")
    run = _rows(tmp_path / "v2.db", "SELECT * FROM runs")[0]
    assert (run["status"], run["error"]) == ("running", None)


def test_writes_from_threads(tmp_path):
    s = Store(tmp_path / "v2.db")
    threads = [threading.Thread(target=lambda n=n: s.start_step("r", f"n{n}", 1)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(_rows(tmp_path / "v2.db", "SELECT id FROM steps")) == 8


def test_storage_errors_never_raise(tmp_path, caplog):
    blocker = tmp_path / "file"
    blocker.write_text("")
    s = Store(blocker / "v2.db")  # parent is a file: cannot open
    assert s.start_step("r", "n", 1) is None
    s.start_run("r")
    s.end_step(None, "ok")
    s.record_hitl_reply(None, "yes")
    s.record_artifacts("r", None, {"a_path": str(blocker)})
    assert "v2 audit store" in caplog.text
