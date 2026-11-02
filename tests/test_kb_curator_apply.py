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


def _reply(plan: dict) -> dict:
    import yaml

    text = "---kb-plan---\n" + yaml.safe_dump(plan) + "\n---kb-plan---"
    return {"text": text, "input_tokens": 1, "output_tokens": 1, "cache_read_tokens": 0,
            "cache_creation_tokens": 0, "cost_usd": 0.0, "duration_s": 1.0}


@pytest.fixture
def llm(monkeypatch, vault):
    calls = []

    def install(*replies):
        def fake(model, system, user, **kw):
            calls.append(user)
            return _reply(replies[len(calls) - 1])
        monkeypatch.setattr(kb_curator, "call_llm", fake)
        monkeypatch.setattr(kb_curator, "_build_system_prompt", lambda: "sys")
        return calls
    return install


def test_incomplete_proceed_plan_gets_one_corrective_retry(llm):
    incomplete = _plan(themes_attached=["t", "u"], theme_updates=[{"slug": "t", "new_body": "# t\n"}])
    del incomplete["book_outline_body"]
    complete = _plan(themes_attached=["t", "u"],
                     theme_updates=[{"slug": "t", "new_body": "# t\n"}, {"slug": "u", "new_body": "# u\n"}])
    calls = llm(incomplete, complete)
    plan, result = kb_curator._call_llm_for_plan({"extraction_report": "r"}, [])
    assert plan == complete and len(calls) == 2
    assert "theme_updates for u" in calls[1] and "book_outline_body" in calls[1]
    assert result["input_tokens"] == 2


def test_retry_still_incomplete_keeps_fallback(llm):
    incomplete = _plan(themes_attached=["t", "u"])
    calls = llm(incomplete, incomplete)
    plan, _ = kb_curator._call_llm_for_plan({"extraction_report": "r"}, [])
    assert plan == incomplete and len(calls) == 2


@pytest.mark.parametrize("plan", [_plan(themes_attached=["t"]), {"action": "NEEDS_APPROVAL_THEME", "proposed_theme": "x"}])
def test_complete_or_approval_plan_needs_no_retry(llm, plan):
    calls = llm(plan)
    assert kb_curator._call_llm_for_plan({"extraction_report": "r"}, [])[0] == plan
    assert len(calls) == 1


def test_book_outline_written_when_present(vault, tmp_path):
    kb_curator._apply_proceed({"run_dir": str(tmp_path / "run")}, _plan())
    assert (vault / "book-outline.md").read_text() == "# outline new\n"
