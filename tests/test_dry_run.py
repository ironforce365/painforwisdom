"""A dry run keeps nothing outside its run folder and can repeat on the same input (spec 2026-11-06-pipeline-dry-run-design)."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from pipeline import dry_run, publishing_config
from pipeline import publish as publish_pkg
from pipeline import telegram
from pipeline.nodes import transcribe

BLOG, RESEARCH = "11111111-1111-1111-1111-111111111111", "22222222-2222-2222-2222-222222222222"


def _sandbox_env(root: Path, blog: str = BLOG, research: str = RESEARCH) -> None:
    (root / ".env.sandbox").write_text(
        f"NOTION_API_KEY=sandbox-key\nNOTION_BLOG_DATA_SOURCE_ID={blog}\nNOTION_RESEARCH_DATA_SOURCE_ID={research}\n"
        "VAULT_PATH=/sandbox/vault\nANTHROPIC_AUTH_TOKEN=not-this-one\n")


def _vault(root: Path) -> Path:
    book = root / "gonzalo-book"
    (book / "entries").mkdir(parents=True)
    (book / "themes").mkdir()
    (root / ".obsidian").mkdir()
    (root / ".obsidian" / "app.json").write_text("{}")
    (book / "entries" / "2026-11-05-already-here.md").write_text("old")
    (book / "entries" / "2026-11-04-other.md").write_text("other")
    (book / "_index.md").write_text("| Date | Entry |\n| 2026-11-05 | [[2026-11-05-already-here]] |\n| 2026-11-04 | [[2026-11-04-other]] |\n")
    (book / "themes" / "focus.md").write_text("## Entries\n| [[2026-11-05-already-here]] | x |\n| [[2026-11-04-other]] | y |\n")
    (book / "book-outline.md").write_text("outline, see [[2026-11-05-already-here]]\n")
    return root


@pytest.fixture
def env(monkeypatch):
    """Whatever activate() sets is undone after the test."""
    for name in (dry_run.FLAG, "VAULT_PATH", "NOTION_API_KEY", "NOTION_BLOG_DATA_SOURCE_ID", "NOTION_RESEARCH_DATA_SOURCE_ID",
                 "TELEGRAM_MESSAGE_PREFIX", "PAINFORWISDOM_THEMES_DB", "TRANSCRIPT_DIR", dry_run.VAULT_SOURCE):
        monkeypatch.setenv(name, "")
        monkeypatch.delenv(name)
    saved = dict(os.environ)
    yield
    os.environ.clear()
    os.environ.update(saved)


def test_DT_S9_activate_points_every_write_at_the_run_folder_and_the_sandbox_tables(tmp_path, env):
    """[DT-S9]"""
    _sandbox_env(tmp_path)
    (tmp_path / "obsidian-vault").mkdir()
    os.environ["NOTION_API_KEY"] = "prod-key"
    rd = tmp_path / "processed" / "rid" / "langgraph"

    dry_run.activate(rd, tmp_path)

    scratch = tmp_path / "processed" / "rid" / "dry-run"
    assert os.environ[dry_run.FLAG] == "1"
    assert Path(os.environ["VAULT_PATH"]) == scratch / "vault"
    assert Path(os.environ[dry_run.VAULT_SOURCE]) == tmp_path / "obsidian-vault"
    assert Path(os.environ["TRANSCRIPT_DIR"]) == scratch / "transcripts"
    assert Path(os.environ["PAINFORWISDOM_THEMES_DB"]) == scratch / "themes.db"
    assert os.environ["NOTION_API_KEY"] == "sandbox-key"
    assert os.environ["NOTION_BLOG_DATA_SOURCE_ID"] == BLOG
    assert os.environ["NOTION_RESEARCH_DATA_SOURCE_ID"] == RESEARCH
    assert "ANTHROPIC_AUTH_TOKEN" not in os.environ or os.environ["ANTHROPIC_AUTH_TOKEN"] != "not-this-one"


@pytest.mark.parametrize("write,problem", [
    (False, "no .env.sandbox"),
    (True, "NOTION_BLOG_DATA_SOURCE_ID"),
])
def test_DT_S9_refuses_without_sandbox_tables(tmp_path, env, write, problem):
    """[DT-S9] A missing id would fall back to the real tables."""
    if write:
        _sandbox_env(tmp_path, blog="")
    with pytest.raises(dry_run.DryRunError, match=problem):
        dry_run.activate(tmp_path / "r" / "langgraph", tmp_path)
    assert dry_run.FLAG not in os.environ


def test_DT_S9_refuses_sandbox_ids_that_are_the_real_tables(tmp_path, env):
    """[DT-S9] Same id with or without dashes."""
    _sandbox_env(tmp_path, research=dry_run.PROD_RESEARCH.replace("-", ""))
    with pytest.raises(dry_run.DryRunError, match="real table"):
        dry_run.activate(tmp_path / "r" / "langgraph", tmp_path)


def test_prod_ids_match_the_notion_client():
    import ast

    source = ast.parse((Path(dry_run.__file__).parent / "notion_client.py").read_text())
    values = {n.targets[0].id: n.value.value for n in source.body
              if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name) and n.targets[0].id.startswith("_DEFAULT_")}
    assert values["_DEFAULT_BLOG_DATA_SOURCE_ID"] == dry_run.PROD_BLOG
    assert values["_DEFAULT_RESEARCH_DATA_SOURCE_ID"] == dry_run.PROD_RESEARCH


def test_DT_S9_scratch_vault_is_a_copy_without_the_videos_own_day(tmp_path, env):
    """[DT-S9] A video that already ran for real must not stop at 'already processed', or collide with its own entry."""
    real = _vault(tmp_path / "real")
    before = {p: p.read_bytes() for p in real.rglob("*") if p.is_file()}
    os.environ[dry_run.VAULT_SOURCE] = str(real)
    scratch = tmp_path / "scratch"
    os.environ["VAULT_PATH"] = str(scratch)

    dry_run.prepare_vault("2026-11-05")

    book = scratch / "gonzalo-book"
    assert not (book / "entries" / "2026-11-05-already-here.md").exists()
    assert (book / "entries" / "2026-11-04-other.md").read_text() == "other"
    assert "already-here" not in (book / "_index.md").read_text()
    assert "2026-11-04-other" in (book / "_index.md").read_text()
    assert "already-here" not in (book / "themes" / "focus.md").read_text()
    assert "2026-11-04-other" in (book / "themes" / "focus.md").read_text()
    assert "already-here" not in (book / "book-outline.md").read_text()
    assert not (scratch / ".obsidian").exists()
    assert {p: p.read_bytes() for p in real.rglob("*") if p.is_file()} == before


def test_prepare_vault_keeps_the_scratch_copy_of_a_resumed_run(tmp_path, env):
    real = _vault(tmp_path / "real")
    os.environ[dry_run.VAULT_SOURCE] = str(real)
    scratch = tmp_path / "scratch"
    os.environ["VAULT_PATH"] = str(scratch)
    dry_run.prepare_vault("2026-11-05")
    (scratch / "gonzalo-book" / "entries" / "2026-11-05-new.md").write_text("written by the run")

    dry_run.prepare_vault("2026-11-05")

    assert (scratch / "gonzalo-book" / "entries" / "2026-11-05-new.md").read_text() == "written by the run"


def test_recorded_reads_run_json(tmp_path):
    assert dry_run.recorded(tmp_path) is False
    (tmp_path / "run.json").write_text("not json")
    assert dry_run.recorded(tmp_path) is False
    (tmp_path / "run.json").write_text(json.dumps({"dry_run": True}))
    assert dry_run.recorded(tmp_path) is True


def test_DT_S9_auto_publishing_never_fires_in_a_dry_run(tmp_path, monkeypatch, env):
    """[DT-S9] Whatever the catalog says: `auto` would publish in the node."""
    config = tmp_path / "publishing.json"
    config.write_text(json.dumps({"language": "en", "youtube": {"mode": "auto"}, "wordpress": {"mode": "off"}}))
    monkeypatch.setattr(publishing_config, "CONFIG_PATH", config)
    assert publishing_config.load("prod")["youtube"]["mode"] == "auto"
    os.environ[dry_run.FLAG] = "1"
    assert publishing_config.load("prod")["youtube"]["mode"] == "manual"
    assert publishing_config.load("prod")["wordpress"]["mode"] == "off"


def test_DT_D11_the_publisher_refuses_a_dry_run(tmp_path):
    """[DT-D11] Last line of defence: the adapter is never reached."""
    (tmp_path / "run.json").write_text(json.dumps({"dry_run": True}))
    with pytest.raises(ValueError, match="dry run"):
        publish_pkg.run_target(tmp_path, "youtube")


def test_DT_S9_telegram_says_dry_run(monkeypatch, env):
    """[DT-S9]"""
    assert telegram._prefix("hi") == "hi"
    os.environ[dry_run.FLAG] = "1"
    assert telegram._prefix("hi") == "[DRY RUN] hi"
    os.environ["TELEGRAM_MESSAGE_PREFIX"] = "[SANDBOX] "
    assert telegram._prefix("hi") == "[DRY RUN] [SANDBOX] hi"


def test_DT_S9_transcript_stays_in_the_run_folder(tmp_path, monkeypatch, env):
    """[DT-S9] The real path leaves a file beside the input, one more each time."""
    video = tmp_path / "inbox" / "PXL_20261105_1.mp4"
    video.parent.mkdir()
    video.write_bytes(b"x")
    out = tmp_path / "scratch" / "transcripts"
    out.mkdir(parents=True)
    (out / "transcript_2026-11-05.txt").write_text("word " * 40)
    os.environ["TRANSCRIPT_DIR"] = str(out)
    monkeypatch.setattr(transcribe, "_run_whisper_shell", lambda *a, **k: type("P", (), {"returncode": 0})())

    state = {"video_path": str(video), "run_dir": str(tmp_path / "rd"), "language": "en", "run_id": "r"}
    result = transcribe.node_transcribe(state)

    assert Path(result["transcript_path"]).parent == out
    assert not (video.parent / "auto-generated").exists()
