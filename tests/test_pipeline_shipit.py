"""The pipeline release script against a throwaway repo with a bare origin; deploy, notify and gh stubbed."""
from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "pipeline/scripts/shipit.sh"
REL = "pipeline/scripts/shipit.sh"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


def _recorder(path: Path, log: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f'#!/bin/sh\necho "$*" >> {log}\n')
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def _make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "dev"
    (repo / "pipeline/scripts").mkdir(parents=True)
    shutil.copy(SCRIPT, repo / REL)
    _git(repo.parent, "init", "-q", "-b", "main", str(repo))
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    _git(repo, "tag", "-a", "v0.1.17", "-m", "coach")
    origin = tmp_path / "origin.git"
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    _git(repo, "remote", "add", "origin", str(origin))
    (repo / "notes.txt").write_text("feature\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "Add feature (#90)")
    _git(repo, "push", "-q", "-u", "origin", "main", "--tags")
    return repo


def _ship(repo: Path, *args: str, deploy_rc: int = 0) -> subprocess.CompletedProcess[str]:
    tmp = repo.parent
    stubs = tmp / "stubs"
    _recorder(stubs / "deploy.sh", tmp / "deploy.log")
    with (stubs / "deploy.sh").open("a") as f:
        f.write(f"exit {deploy_rc}\n")
    _recorder(stubs / "notify.sh", tmp / "notify.log")
    _recorder(stubs / "bin" / "gh", tmp / "gh.log")
    env = {**os.environ,
           "PIPELINE_SHIPIT_DEPLOY_OVERRIDE": str(stubs / "deploy.sh"),
           "PIPELINE_SHIPIT_NOTIFY_OVERRIDE": str(stubs / "notify.sh"),
           "PIPELINE_SHIPIT_LOCK_PATH": str(tmp / "ship.lock"),
           "PATH": f"{stubs / 'bin'}{os.pathsep}{os.environ['PATH']}"}
    return subprocess.run(["bash", str(repo / REL), *args], cwd=str(repo), env=env,
                          capture_output=True, text=True)


def test_first_ship_tags_main_commit_with_no_branch_or_bump(tmp_path):
    repo = _make_repo(tmp_path)
    r = _ship(repo)
    assert r.returncode == 0, r.stdout + r.stderr
    origin = tmp_path / "origin.git"
    assert _git(origin, "rev-parse", "pipeline-v0.1.0^{commit}") == _git(origin, "rev-parse", "main")
    assert _git(origin, "cat-file", "-t", "pipeline-v0.1.0") == "tag"
    assert _git(origin, "branch", "--list", "release/*") == ""
    assert (tmp_path / "deploy.log").read_text().strip() == "pipeline-v0.1.0"
    assert "release create pipeline-v0.1.0" in (tmp_path / "gh.log").read_text()
    assert "COMPLETE" in (tmp_path / "notify.log").read_text()


def test_next_ship_bumps_from_pipeline_tags_not_coach_tags(tmp_path):
    repo = _make_repo(tmp_path)
    assert _ship(repo).returncode == 0
    r = _ship(repo, "minor")
    assert r.returncode == 0, r.stdout + r.stderr
    assert (tmp_path / "deploy.log").read_text().split() == ["pipeline-v0.1.0", "pipeline-v0.2.0"]


def test_refuses_when_head_is_not_origin_main(tmp_path):
    repo = _make_repo(tmp_path)
    (repo / "local.txt").write_text("x\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "unpushed")
    r = _ship(repo)
    assert r.returncode != 0 and "HEAD is not origin/main" in r.stderr
    assert not (tmp_path / "deploy.log").exists()


def test_dry_run_changes_nothing(tmp_path):
    repo = _make_repo(tmp_path)
    r = _ship(repo, "--dry-run")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "pipeline-v0.1.0" in r.stdout
    assert _git(repo, "tag", "-l", "pipeline-v*") == ""
    assert not (tmp_path / "deploy.log").exists()


def test_deploy_failure_keeps_tag_and_notifies(tmp_path):
    repo = _make_repo(tmp_path)
    r = _ship(repo, deploy_rc=3)
    assert r.returncode != 0 and "deploy-live.sh failed" in r.stderr
    assert _git(tmp_path / "origin.git", "tag", "-l", "pipeline-v0.1.0") == "pipeline-v0.1.0"
    assert "FAILED" in (tmp_path / "notify.log").read_text()
