"""A dry run through the launcher, the API and the real entry points (spec 2026-11-06-pipeline-dry-run-design)."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from pipeline import dry_run, runtime
from pipeline.publish import package
from pipeline.ui import launcher, publish, runs
from pipeline.ui.app import create_app
from pipeline.ui.settings import Settings

pytestmark = pytest.mark.integration

FIX = Path(__file__).parent / "fixtures" / "ui"
REPO = Path(__file__).resolve().parent.parent
BLOG, RESEARCH = "11111111-1111-1111-1111-111111111111", "22222222-2222-2222-2222-222222222222"


def _sandbox_env(root: Path) -> None:
    (root / ".env.sandbox").write_text(f"NOTION_API_KEY=sandbox-key\nNOTION_BLOG_DATA_SOURCE_ID={BLOG}\nNOTION_RESEARCH_DATA_SOURCE_ID={RESEARCH}\n")


@pytest.fixture
def processed(tmp_path) -> Path:
    shutil.copytree(FIX / "processed", tmp_path / "processed")
    return tmp_path / "processed"


@pytest.fixture
def settings(tmp_path, processed) -> Settings:
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "transcript_2026-11-05.txt").write_text("words " * 40)
    return Settings(processed_root=processed, inbox=inbox, runner="fake", profile="prod")


@pytest.fixture
def spawned(monkeypatch) -> list:
    calls: list = []

    def fake_spawn(argv, log_path):
        calls.append(argv)
        return os.getpid()

    monkeypatch.setattr(launcher, "spawn", fake_spawn)
    return calls


def _client(settings: Settings) -> TestClient:
    return TestClient(create_app(settings), base_url="http://127.0.0.1")


def _mark_dry(processed: Path, run_id: str) -> Path:
    rd = processed / run_id / "langgraph"
    runs.update_record(rd, dry_run=True)
    return rd


def test_DT_S9_start_records_and_passes_the_dry_run(settings, spawned):
    """[DT-S9]"""
    body = _client(settings).post("/api/runs", json={"input": "transcript_2026-11-05.txt", "dry_run": True})
    assert body.status_code == 201
    assert "--dry-run" in spawned[0]
    run_id = body.json()["run_id"]
    assert json.loads((settings.processed_root / run_id / "langgraph" / "run.json").read_text())["dry_run"] is True
    assert _client(settings).get(f"/api/runs/{run_id}").json()["dry_run"] is True


def test_a_normal_run_is_not_a_dry_run(settings, spawned):
    run_id = _client(settings).post("/api/runs", json={"input": "transcript_2026-11-05.txt"}).json()["run_id"]
    assert "--dry-run" not in spawned[0]
    assert "dry_run" not in json.loads((settings.processed_root / run_id / "langgraph" / "run.json").read_text())


def test_DT_S9_the_real_runner_refuses_without_sandbox_tables(settings, spawned, tmp_path, monkeypatch):
    """[DT-S9] The console says why, instead of a child that dies at once."""
    monkeypatch.setattr(runtime, "STATE_ROOT", tmp_path)
    real = Settings(processed_root=settings.processed_root, inbox=settings.inbox, runner="real", profile="prod")
    body = _client(real).post("/api/runs", json={"input": "transcript_2026-11-05.txt", "dry_run": True})
    assert body.status_code == 400
    assert ".env.sandbox" in body.json()["detail"]
    assert spawned == []


def test_DT_S9_a_dry_run_does_not_count_as_already_ran(settings, processed):
    """[DT-S9] The tile's warning would say otherwise, and nothing stayed."""
    rd = processed / "fx-done" / "langgraph"
    runs.update_record(rd, input={"kind": "transcript", "path": str(settings.inbox / "transcript_2026-11-05.txt")})
    assert launcher.list_inputs(settings.inbox, processed)[0]["previous_runs"] == ["fx-done"]
    runs.update_record(rd, dry_run=True)
    assert launcher.list_inputs(settings.inbox, processed)[0]["previous_runs"] == []


def test_DT_D11_restart_stays_dry_and_run_for_real_does_not(settings, spawned, processed):
    """[DT-D11]"""
    _mark_dry(processed, "fx-done")
    rd = processed / "fx-done" / "langgraph"
    source = settings.inbox / "transcript_2026-11-05.txt"
    runs.update_record(rd, input={"kind": "transcript", "path": str(source)}, status="done")
    client = _client(settings)
    assert client.post("/api/runs/fx-done/restart", json={}).status_code == 201
    assert "--dry-run" in spawned[-1]
    for run in runs.list_runs(processed):  # one run at a time: the restart reads as active
        if run["status"] in runs.ACTIVE:
            runs.update_record(processed / run["run_id"] / "langgraph", status="done")
    assert client.post("/api/runs/fx-done/restart", json={"dry_run": False}).status_code == 201
    assert "--dry-run" not in spawned[-1]


def test_DT_D11_publishing_a_dry_run_is_refused_and_it_asks_nothing(settings, processed):
    """[DT-D11] The package is there to look at: not in needs-you, and the console will not publish it."""
    rd = processed / "fx-done" / "langgraph"
    assert [p for p in runs.load_run(processed, "fx-done", [])["waiting_packages"]]
    wanted = next(r for r in runs.list_runs(processed) if r["run_id"] == "fx-done")["attention"]
    assert any(item["kind"] == "publish" for item in wanted)

    _mark_dry(processed, "fx-done")

    assert runs.load_run(processed, "fx-done", [])["waiting_packages"] == []
    row = next(r for r in runs.list_runs(processed) if r["run_id"] == "fx-done")
    assert row["dry_run"] is True
    assert row["attention"] == []
    target = next(t for t in package.NODE_DIRS if package.read(rd, t))
    with pytest.raises(publish.PublishError) as refused:
        publish.confirm(settings, "fx-done", target, {})
    assert refused.value.status == 409
    assert "dry run" in refused.value.body["detail"]


def test_DT_D11_what_a_dry_run_needs_says_so(processed):
    """[DT-D11] A failed dry run still needs you, and reads as a dry run."""
    rd = _mark_dry(processed, "fx-crashed")
    runs.update_record(rd, status="failed", error="boom")
    row = next(r for r in runs.list_runs(processed) if r["run_id"] == "fx-crashed")
    assert row["attention"] and all(item["label"].startswith("Dry run: ") for item in row["attention"])


# --- the real entry points, as processes ------------------------------------------------------------------------------


def _env(state_root: Path) -> dict:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("NOTION_", "TELEGRAM_", "VAULT_", "PIPELINE_"))}
    return {**env, "PAINFORWISDOM_STATE_ROOT": str(state_root), "CHECKPOINT_DB_PATH": str(state_root / "ck.db"), "PYTHONPATH": str(REPO)}


def _state_root(tmp_path: Path) -> Path:
    root = tmp_path / "state"
    entries = root / "obsidian-vault" / "gonzalo-book" / "entries"
    entries.mkdir(parents=True)
    (entries / "2026-11-05-real.md").write_text("real entry")
    _sandbox_env(root)
    return root


# Runs the module as the program up to its imports, then reports what the nodes would use.
_REPORT = """
import json, sys
sys.argv = {argv!r}

exec(compile(open({path!r}).read().split("# Heavy imports must come after env load.")[0].split("from pipeline import hitl  # noqa: E402")[0], {path!r}, "exec"), {{"__name__": "__main__", "__file__": {path!r}}})
from pipeline.nodes import kb_curator
from pipeline import notion_client, publishing_config, themes_db
print(json.dumps({{"vault": str(kb_curator.VAULT_ROOT), "blog": notion_client.BLOG_DATA_SOURCE_ID, "research": notion_client.RESEARCH_DATA_SOURCE_ID, "themes": str(themes_db._db_path())}}))
"""


@pytest.mark.parametrize("module,path,seed", [
    ("pipeline.run", REPO / "pipeline" / "run.py", False),
    ("pipeline.retry", REPO / "pipeline" / "retry.py", True),
])
def test_DT_S9_every_module_reads_the_scratch_paths(tmp_path, module, path, seed):
    """[DT-S9] They read env at import, so the order in the entry point is the whole safety."""
    root = _state_root(tmp_path)
    processed = tmp_path / "processed"
    rd = processed / "rid" / "langgraph"
    argv = [module, "--run-id", "rid", "--processed-root", str(processed)]
    if seed:
        rd.mkdir(parents=True)
        (rd / "run.json").write_text(json.dumps({"dry_run": True}))
    else:
        argv.append("--dry-run")
    code = _REPORT.format(argv=argv, module=module, path=str(path))

    out = subprocess.run([sys.executable, "-c", code], env=_env(root), cwd=REPO, capture_output=True, text=True, timeout=120)

    assert out.returncode == 0, out.stderr
    seen = json.loads(out.stdout.strip().splitlines()[-1])
    scratch = processed / "rid" / "dry-run"
    assert seen == {"vault": str(scratch / "vault" / "gonzalo-book"), "blog": BLOG, "research": RESEARCH, "themes": str(scratch / "themes.db")}


def test_DT_S9_the_cli_leaves_the_real_vault_alone(tmp_path):
    """[DT-S9] As far as a run goes with no graph to call: its record and the scratch copy, without the day's entry."""
    root = _state_root(tmp_path)
    processed = tmp_path / "processed"
    missing = tmp_path / "transcript_2026-11-05.txt"
    argv = [sys.executable, "-m", "pipeline.run", "--dry-run", "--run-id", "rid", "--processed-root", str(processed), "--from-transcript", str(missing)]

    subprocess.run(argv, env=_env(root), cwd=REPO, capture_output=True, text=True, timeout=120)

    assert (root / "obsidian-vault" / "gonzalo-book" / "entries" / "2026-11-05-real.md").read_text() == "real entry"
    assert (processed / "rid" / "dry-run" / "vault" / "gonzalo-book" / "entries").is_dir()
    assert list((processed / "rid" / "dry-run" / "vault" / "gonzalo-book" / "entries").iterdir()) == []
    assert dry_run.recorded(processed / "rid" / "langgraph")


def test_DT_S9_the_cli_refuses_a_batch_and_a_missing_sandbox(tmp_path):
    """[DT-S9]"""
    root = _state_root(tmp_path)
    batch = subprocess.run([sys.executable, "-m", "pipeline.run", "--dry-run", "--dir", str(tmp_path)], env=_env(root), cwd=REPO, capture_output=True, text=True, timeout=120)
    assert batch.returncode != 0 and "--dir" in batch.stderr
    (root / ".env.sandbox").unlink()
    alone = subprocess.run([sys.executable, "-m", "pipeline.run", "--dry-run", "--from-transcript", "x.txt"], env=_env(root), cwd=REPO, capture_output=True, text=True, timeout=120)
    assert alone.returncode != 0 and ".env.sandbox" in alone.stderr


def test_transcribe_script_keeps_the_input_in_a_dry_run(tmp_path):
    """The low-confidence branch moves the input in a real run. Checked on the function, with no Whisper to run."""
    text = (REPO / "extract_transcription.sh").read_text()
    body = text[text.index("quarantine_file()"):]
    body = body[:body.index("\n}\n") + 3]
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    script = f'set -u\nINPUT_FILE={video}\nDRY_RUN=1\nQUARANTINE_DIR={tmp_path}/q\nTARGET_TRANSCRIPT_FILENAME={tmp_path}/t\nTMP_DIR={tmp_path}\nTEMP_AUDIO_FILE_PATH={tmp_path}/a.mp3\nTELEGRAM=""\n{body}\nquarantine_file "QUALITY=LOW"\n'
    out = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    assert video.is_file()
    assert not (tmp_path / "q").exists()
