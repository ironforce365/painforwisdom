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
