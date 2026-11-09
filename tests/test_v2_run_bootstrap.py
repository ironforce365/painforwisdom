"""pipeline.v2.run bootstrap: where the audit DB goes per profile (spec 2026-11-09-session-pipeline-v2 §3, §5.1; Q3915, Q3917)."""
from __future__ import annotations

import os

import pytest

from pipeline.v2 import run

ENV = ("PIPELINE_V2_DB", "PIPELINE_PROFILE", "TELEGRAM_MESSAGE_PREFIX", "PIPELINE_DRY_RUN", "VAULT_PATH",
       "TRANSCRIPT_DIR", "PAINFORWISDOM_THEMES_DB", "PIPELINE_DRY_RUN_VAULT_SOURCE", "NOTION_API_KEY",
       "NOTION_BLOG_DATA_SOURCE_ID", "NOTION_RESEARCH_DATA_SOURCE_ID")


@pytest.fixture
def root(tmp_path, monkeypatch):
    for name in ENV:  # registered so monkeypatch restores whatever bootstrap writes
        monkeypatch.setenv(name, "x")
        monkeypatch.delenv(name)
    monkeypatch.setenv("PAINFORWISDOM_STATE_ROOT", str(tmp_path))
    (tmp_path / ".env.sandbox").write_text(
        "NOTION_API_KEY=k\nNOTION_BLOG_DATA_SOURCE_ID=blog-sandbox\nNOTION_RESEARCH_DATA_SOURCE_ID=research-sandbox\n")
    return tmp_path


def _args(argv):
    return run._parser().parse_args(["--from-transcript", "t.txt", *argv])


def test_sandbox_unset_uses_its_own_db(root):
    run.bootstrap(_args(["--profile", "sandbox"]), "rid")
    assert os.environ["PIPELINE_V2_DB"] == str(run.PROJECT_ROOT / "pipeline" / "v2-sandbox.db")
    assert os.environ["TELEGRAM_MESSAGE_PREFIX"] == "[SANDBOX] "


def test_sandbox_env_file_wins(root):
    (root / ".env.sandbox").write_text(f"PIPELINE_V2_DB={root / 'mine.db'}\n")
    run.bootstrap(_args(["--profile", "sandbox"]), "rid")
    assert os.environ["PIPELINE_V2_DB"] == str(root / "mine.db")


def test_dry_run_keeps_the_db_in_the_run_folder(root):
    run.bootstrap(_args(["--dry-run", "--processed-root", str(root / "p")]), "rid")
    assert os.environ["PIPELINE_V2_DB"] == str(root / "p" / "rid" / "dry-run" / "v2.db")
    assert os.environ["VAULT_PATH"] == str(root / "p" / "rid" / "dry-run" / "vault")


def test_prod_unset_leaves_the_spec_default(root):
    run.bootstrap(_args([]), "rid")
    assert "PIPELINE_V2_DB" not in os.environ  # store.default_path(): pipeline/v2.db, never the v1 checkpoints.db


def test_no_dir_flag():
    with pytest.raises(SystemExit):
        run._parser().parse_args(["--dir", "videos"])
