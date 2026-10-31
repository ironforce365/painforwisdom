"""ship-it.sh against a throwaway repo with a bare origin; deploy, notify and gh stubbed."""
from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

SHIP_IT = Path(__file__).resolve().parents[1] / "scripts" / "ship-it.sh"
PYPROJECT = "services/coach/pyproject.toml"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


def _recorder(path: Path, log: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f'#!/bin/sh\necho "$*" >> {log}\n')
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def _make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "dev"
    (repo / "services/coach/scripts").mkdir(parents=True)
    shutil.copy(SHIP_IT, repo / "services/coach/scripts/ship-it.sh")
    (repo / PYPROJECT).write_text('[project]\nname = "painforwisdom-coach"\nversion = "0.1.15"\n')
    (repo / "ASSUMPTIONS.md").write_text("# ASSUMPTIONS\n")
    _git(repo.parent, "init", "-q", "-b", "main", str(repo))
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    _git(repo, "tag", "-a", "v0.1.15", "-m", "old")
    origin = tmp_path / "origin.git"
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    _git(repo, "remote", "add", "origin", str(origin))
    _git(repo, "push", "-q", "-u", "origin", "main", "--tags")
    (repo / "notes.txt").write_text("feature\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "Add feature (#90)")
    _git(repo, "push", "-q", "origin", "main")
    return repo


def _ship(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    stubs = repo.parent / "stubs"
    _recorder(stubs / "deploy.sh", repo.parent / "deploy.log")
    _recorder(stubs / "notify.sh", repo.parent / "notify.log")
    _recorder(stubs / "bin" / "gh", repo.parent / "gh.log")
    env = {**os.environ,
           "COACH_SHIPIT_DEPLOY_OVERRIDE": str(stubs / "deploy.sh"),
           "COACH_SHIPIT_NOTIFY_OVERRIDE": str(stubs / "notify.sh"),
           "COACH_SHIPIT_LOCK_PATH": str(repo.parent / "ship.lock"),
           "PATH": f"{stubs / 'bin'}{os.pathsep}{os.environ['PATH']}"}
    return subprocess.run(["bash", str(repo / "services/coach/scripts/ship-it.sh"), *args],
                          cwd=str(repo), env=env, capture_output=True, text=True)


def test_ships_next_patch_on_its_own_release_branch(tmp_path):
    repo = _make_repo(tmp_path)
    (repo / "ASSUMPTIONS.md").write_text("drift\n")  # OS-managed: must not block
    r = _ship(repo)
    assert r.returncode == 0, r.stdout + r.stderr
    origin = tmp_path / "origin.git"
    assert _git(origin, "rev-parse", "release/0.1.16") == _git(origin, "rev-parse", "v0.1.16^{commit}")
    assert 'version = "0.1.16"' in _git(origin, "show", f"v0.1.16:{PYPROJECT}")
    assert 'version = "0.1.15"' in _git(origin, "show", f"main:{PYPROJECT}")
    assert (tmp_path / "deploy.log").read_text().strip() == "v0.1.16"
    gh = (tmp_path / "gh.log").read_text()
    assert "release create v0.1.16" in gh
    assert "COMPLETE" in (tmp_path / "notify.log").read_text()


def test_refuses_when_head_is_not_origin_main(tmp_path):
    repo = _make_repo(tmp_path)
    (repo / "local.txt").write_text("x\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "unpushed")
    r = _ship(repo)
    assert r.returncode != 0 and "HEAD is not origin/main" in r.stderr
    assert not (tmp_path / "deploy.log").exists()


def test_authored_edit_blocks(tmp_path):
    repo = _make_repo(tmp_path)
    (repo / "notes.txt").write_text("edited\n")
    r = _ship(repo)
    assert r.returncode != 0 and "notes.txt" in r.stderr


def test_dry_run_changes_nothing(tmp_path):
    repo = _make_repo(tmp_path)
    r = _ship(repo, "--dry-run", "minor")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "v0.2.0" in r.stdout
    assert _git(tmp_path / "origin.git", "tag", "-l", "v0.2.0") == ""
    assert not (tmp_path / "deploy.log").exists()
