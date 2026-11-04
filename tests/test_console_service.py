"""install-pipeline-units.sh and deploy-live.sh in a tmp tree with git, docker, curl, systemctl and sleep stubbed."""
from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
INSTALLER = REPO / "pipeline/scripts/install-pipeline-units.sh"
DEPLOY = REPO / "services/coach/scripts/deploy-live.sh"
CONSOLE_UNIT = "painforwisdom-console.service"
STUBS = {
    "git": 'case "$*" in *"branch -r"*) echo "  origin/release/1.2.3";; *rev-parse*) echo abc123;; esac\n',
    "docker": "",
    "curl": 'case "$*" in *api/graph*) exit "${GRAPH_RC:-0}";; esac\n',
    "systemctl": "",
    "sleep": "",
}


class Tree:
    def __init__(self, tmp: Path) -> None:
        self.tmp = tmp
        self.home = tmp / "home"
        self.live = tmp / "live"
        self.state = tmp / "state"
        self.python = tmp / "conda/bin/python"
        self.log = tmp / "calls.log"
        self.unit_dir = self.home / ".config/systemd/user"
        self.home.mkdir()
        self.state.mkdir()
        (self.state / ".env").write_text("X=1\n")
        (self.state / ".env").chmod(0o644)
        (self.live / ".git").mkdir(parents=True)
        (self.live / "pipeline").mkdir()
        (self.live / "pipeline/runtime.py").write_text("PAINFORWISDOM_STATE_ROOT\n")
        shutil.copytree(REPO / "pipeline/scripts", self.live / "pipeline/scripts",
                        ignore=lambda _d, names: [n for n in names if n != INSTALLER.name])
        shutil.copytree(REPO / "pipeline/systemd", self.live / "pipeline/systemd")
        (self.live / "services/coach").mkdir(parents=True)
        (self.live / "services/coach/.env.coach").write_text("")
        bin_dir = tmp / "bin"
        bin_dir.mkdir()
        for name, body in STUBS.items():
            stub = bin_dir / name
            stub.write_text(f'#!/bin/sh\necho "{name} $*" >> {self.log}\n{body}')
            stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
        self.env = {**os.environ, "HOME": str(self.home), "COACH_LIVE_DIR": str(self.live),
                    "PFW_LIVE_DIR": str(self.live), "PFW_STATE_ROOT": str(self.state),
                    "PFW_PYTHON": str(self.python), "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"}
        self.env.pop("PFW_PROCESSED_ROOT", None)
        self.env.pop("DEPLOY_IGNORE_ACTIVE_RUN", None)

    def calls(self) -> list[str]:
        return self.log.read_text().splitlines() if self.log.exists() else []

    def install(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["bash", str(INSTALLER), *args], env=self.env, capture_output=True, text=True)

    def deploy(self, **env: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["bash", str(DEPLOY), "v1.2.3"], env={**self.env, **env},
                              capture_output=True, text=True)

    def run_json(self, run_id: str, status: str, pid: int, root: Path | None = None) -> None:
        path = (root or self.live / "processed") / run_id / "langgraph/run.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"run_id": run_id, "status": status, "pid": pid}))


@pytest.fixture
def tree(tmp_path: Path) -> Tree:
    return Tree(tmp_path)


def _assert_rendered(text: str, tree: Tree) -> None:
    assert "@" not in text
    assert f"WorkingDirectory={tree.live}\n" in text
    assert f"Environment=PAINFORWISDOM_STATE_ROOT={tree.state}\n" in text
    assert f"Environment=PATH={tree.python.parent}:%h/.local/bin:/usr/local/bin:/usr/bin:/bin\n" in text
    assert f"ExecStart={tree.python} -m pipeline.ui --profile prod\n" in text
    for line in ("Restart=always", "RestartSec=5", "KillMode=process", "WantedBy=default.target"):
        assert f"{line}\n" in text


def test_dry_run_prints_plan_and_unit_and_touches_nothing(tree: Tree) -> None:
    r = tree.install("--dry-run")
    assert r.returncode == 0, r.stderr
    _assert_rendered(r.stdout, tree)
    assert "enable --now" in r.stdout
    assert tree.calls() == []
    assert not tree.unit_dir.exists()
    assert stat.S_IMODE((tree.state / ".env").stat().st_mode) == 0o644


def test_unit_dir_writes_units_and_never_calls_systemctl(tree: Tree) -> None:
    out = tree.tmp / "units"
    r = tree.install("--unit-dir", str(out))
    assert r.returncode == 0, r.stderr
    _assert_rendered((out / CONSOLE_UNIT).read_text(), tree)
    assert (out / "painforwisdom-daily-brief.timer").exists()
    assert (out / "painforwisdom-augment-research.service").exists()
    assert tree.calls() == []
    assert not tree.unit_dir.exists()


def test_default_enables_now_and_restarts_console_never_timers(tree: Tree) -> None:
    r = tree.install()
    assert r.returncode == 0, r.stderr
    assert tree.calls()[:3] == [
        "systemctl --user daemon-reload",
        f"systemctl --user enable --now {CONSOLE_UNIT}",
        f"systemctl --user restart {CONSOLE_UNIT}",
    ]
    assert not any("timer" in c and "enable" in c for c in tree.calls())
    assert stat.S_IMODE((tree.state / ".env").stat().st_mode) == 0o600


def test_no_restart_enables_only(tree: Tree) -> None:
    assert tree.install("--no-restart").returncode == 0
    assert f"systemctl --user enable {CONSOLE_UNIT}" in tree.calls()
    assert not any("--now" in c or "restart" in c for c in tree.calls())


@pytest.mark.parametrize("missing", ["pipeline/runtime.py", ".env"])
def test_dry_run_still_runs_prechecks_on_overridden_paths(tree: Tree, missing: str) -> None:
    (tree.live / missing if missing != ".env" else tree.state / missing).unlink()
    r = tree.install("--dry-run")
    assert r.returncode == 1
    assert "ERROR" in r.stderr


def test_unknown_flag_refused(tree: Tree) -> None:
    assert tree.install("--nope").returncode == 2
    assert tree.calls() == []


def test_deploy_installs_then_restarts_console_after_coach_health(tree: Tree) -> None:
    r = tree.deploy()
    assert r.returncode == 0, r.stdout + r.stderr
    calls = tree.calls()
    order = [calls.index(c) for c in (
        "docker compose -p coach --project-directory " + str(tree.live / "services/coach") + " --env-file "
        + str(tree.live / "services/coach/.env.coach") + " -f " + str(tree.live / "services/coach/docker-compose.yml")
        + " up -d",
        "curl -fsS -m 5 http://127.0.0.1:8800/health",
        f"systemctl --user enable {CONSOLE_UNIT}",
        f"systemctl --user restart {CONSOLE_UNIT}",
        "curl -fsS -m 2 http://127.0.0.1:8820/api/graph",
    )]
    assert order == sorted(order)
    _assert_rendered((tree.unit_dir / CONSOLE_UNIT).read_text(), tree)


@pytest.mark.parametrize("status", ["running", "waiting_hitl", "waiting_reply"])
def test_deploy_refuses_active_run_before_any_git_call(tree: Tree, status: str) -> None:
    tree.run_json("run-1", status, os.getpid())
    r = tree.deploy()
    assert r.returncode == 1
    assert "pipeline run run-1 active; retry after it ends" in r.stdout
    assert tree.calls() == []


def test_deploy_reads_overridden_processed_root(tree: Tree) -> None:
    root = tree.tmp / "elsewhere"
    tree.run_json("run-2", "running", os.getpid(), root)
    assert tree.deploy().returncode == 0
    r = tree.deploy(PFW_PROCESSED_ROOT=str(root))
    assert r.returncode == 1
    assert "pipeline run run-2 active" in r.stdout


def test_deploy_override_ignores_active_run(tree: Tree) -> None:
    tree.run_json("run-1", "running", os.getpid())
    r = tree.deploy(DEPLOY_IGNORE_ACTIVE_RUN="1")
    assert r.returncode == 0, r.stdout + r.stderr


def test_deploy_ignores_dead_finished_and_unreadable_runs(tree: Tree) -> None:
    tree.run_json("dead-pid", "running", 0)
    tree.run_json("finished", "done", os.getpid())
    bad = tree.live / "processed/bad/langgraph/run.json"
    bad.parent.mkdir(parents=True)
    bad.write_text("{not json")
    r = tree.deploy()
    assert r.returncode == 0, r.stdout + r.stderr


def test_deploy_fails_when_console_never_answers(tree: Tree) -> None:
    r = tree.deploy(GRAPH_RC="7")
    assert r.returncode == 1
    assert "FAIL: console timeout" in r.stdout
    assert tree.calls().count("curl -fsS -m 2 http://127.0.0.1:8820/api/graph") == 30
    assert f"systemctl --user restart {CONSOLE_UNIT}" in tree.calls()
