"""kb_curator._apply_proceed plan validation (defect found by spec 2026-10-31-pipeline-console-ui §10 real runs)."""
from __future__ import annotations

import pytest

from pipeline.nodes import kb_curator


def _plan(**over):
    plan = {
        "action": "PROCEED", "entry_slug": "2026-04-14-x", "entry_body": "# entry\n",
        "timeline_row": "| 2026-04-14 | [[2026-04-14-x]] | t | Weak | i |",
        "theme_updates": [{"slug": "t", "new_body": "# t\n"}], "framework_updates": [],
        "book_outline_body": "# outline new\n", "curator_summary": "ok",
    }
    plan.update(over)
    return plan


@pytest.fixture
def vault(tmp_path, monkeypatch):
    root = tmp_path / "vault"
    root.mkdir()
    (root / "book-outline.md").write_text("# outline old\n")
    monkeypatch.setattr(kb_curator, "VAULT_ROOT", root)
    return root


def _files(root):
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file())


@pytest.mark.parametrize("over", [
    {"entry_body": None},
    {"timeline_row": None},
    {"theme_updates": [{"slug": "t", "new_body": None}]},
    {"framework_updates": [{"slug": "f"}]},
])
def test_invalid_plan_writes_nothing(vault, tmp_path, over):
    with pytest.raises(RuntimeError, match="kb-curator: invalid plan"):
        kb_curator._apply_proceed({"run_dir": str(tmp_path / "run")}, _plan(**over))
    assert _files(vault) == ["book-outline.md"]
    assert (vault / "book-outline.md").read_text() == "# outline old\n"


@pytest.mark.parametrize("over", [{"book_outline_body": None}, {}])
def test_missing_book_outline_keeps_current(vault, tmp_path, over):
    plan = _plan(**over)
    if not over:
        del plan["book_outline_body"]
    out = kb_curator._apply_proceed({"run_dir": str(tmp_path / "run")}, plan)
    assert (vault / "book-outline.md").read_text() == "# outline old\n"
    assert (vault / "entries" / "2026-04-14-x.md").read_text() == "# entry\n"
    assert out["vault_entry_slug"] == "2026-04-14-x"


def test_book_outline_written_when_present(vault, tmp_path):
    kb_curator._apply_proceed({"run_dir": str(tmp_path / "run")}, _plan())
    assert (vault / "book-outline.md").read_text() == "# outline new\n"


THEME = """# T

**First appeared:** 2026-01-01
**Last updated:** 2026-01-01
**Entry count:** 1

## Core tension
tension

## Key insight so far
old insight

## Entries
| Date | Entry | Core Insight |
|------|-------|--------------|
| 2026-01-01 | [[2026-01-01-a]] | a |

## Patterns emerging
old patterns

## Possible chapter angle
angle
"""

ROW = "| 2026-04-14 | [[2026-04-14-x]] | x |"


@pytest.fixture
def themed(vault):
    (vault / "themes").mkdir()
    (vault / "themes" / "t.md").write_text(THEME)
    return vault


def test_existing_theme_append_style(themed, tmp_path):
    upd = {"slug": "t", "entries_row": ROW, "patterns_emerging": "new patterns\n"}
    kb_curator._apply_proceed({"run_dir": str(tmp_path / "run"), "video_date": "2026-04-14"},
                              _plan(theme_updates=[upd]))
    body = (themed / "themes" / "t.md").read_text()
    assert f"| 2026-01-01 | [[2026-01-01-a]] | a |\n{ROW}\n\n## Patterns emerging\nnew patterns\n\n## Possible" in body
    assert "**Entry count:** 2" in body and "**Last updated:** 2026-04-14" in body
    assert "old insight" in body and "angle" in body


@pytest.mark.parametrize("upd", [
    {"slug": "t", "new_body": "# t\n"},  # full rewrite of an existing theme
    {"slug": "t"},  # no entries_row
])
def test_existing_theme_rejects_rewrite_or_missing_row(themed, tmp_path, upd):
    with pytest.raises(RuntimeError, match="kb-curator: invalid plan"):
        kb_curator._apply_proceed({"run_dir": str(tmp_path / "run")}, _plan(theme_updates=[upd]))
    assert (themed / "themes" / "t.md").read_text() == THEME
    assert not (themed / "entries").exists()


def test_snapshot_has_full_theme_and_outline(themed):
    (themed / "book-outline.md").write_text("# outline\n" + "x" * 5000 + "END")
    snap = kb_curator._vault_snapshot()
    assert "themes/t.md (current)" in snap and "old patterns" in snap and "xEND" in snap
