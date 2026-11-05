"""Live-checkout runs read state from PAINFORWISDOM_STATE_ROOT.

The tag-pinned live checkout has no .env and an empty obsidian-vault; run.py
must load .env from the state root, the vault must default there, and a
Telegram ask that fails instantly must not spin the HITL reminder loop.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

os.environ.setdefault("CHECKPOINT_DB_PATH", str(Path(tempfile.mkdtemp()) / "checkpoints.db"))

pytestmark = pytest.mark.integration

REPO = Path(__file__).resolve().parent.parent


def _child_env(state_root: Path) -> dict:
    env = {k: v for k, v in os.environ.items() if k not in ("VAULT_PATH", "STATE_ROOT_PROBE")}
    env["PAINFORWISDOM_STATE_ROOT"] = str(state_root)
    env["CHECKPOINT_DB_PATH"] = str(state_root / "checkpoints.db")
    return env


@pytest.mark.parametrize("module", ["pipeline.run", "pipeline.retry", "pipeline.backfill_wordpress"])
def test_entrypoints_load_env_from_state_root(tmp_path: Path, module: str) -> None:
    (tmp_path / ".env").write_text("STATE_ROOT_PROBE=from-state-root\n")
    code = (
        "import os, sys; sys.argv = ['x', '--profile', 'prod']; "
        f"import {module}; print(os.environ.get('STATE_ROOT_PROBE'))"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], cwd=REPO, env=_child_env(tmp_path),
        capture_output=True, text=True, timeout=120,
    )
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip().splitlines()[-1] == "from-state-root"


def test_vault_defaults_under_state_root(tmp_path: Path) -> None:
    code = "from pipeline import runtime; print(runtime.VAULT_PATH)"
    out = subprocess.run(
        [sys.executable, "-c", code], cwd=REPO, env=_child_env(tmp_path),
        capture_output=True, text=True, timeout=60,
    )
    assert out.returncode == 0, out.stderr
    assert Path(out.stdout.strip()) == (tmp_path / "obsidian-vault").resolve()


def test_telegram_io_falls_back_to_state_root_env(tmp_path: Path) -> None:
    """telegram_io.sh with no token in its own dir or the caller env reads the state root .env."""
    (tmp_path / ".env").write_text("TELEGRAM_BOT_TOKEN=probe-token\nTELEGRAM_CHAT_ID=probe-chat\n")
    script = tmp_path / "bin" / "telegram_io.sh"
    script.parent.mkdir()
    script.write_text((REPO / "telegram_io.sh").read_text())
    script.chmod(0o755)
    env = _child_env(tmp_path)
    for k in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
        env.pop(k, None)
    out = subprocess.run([str(script), "bogus-command"], env=env, capture_output=True, text=True, timeout=30)
    assert "must be set" not in out.stderr, out.stderr
