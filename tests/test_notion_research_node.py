"""notion-research runs.jsonl row carries page_ids (spec 2026-10-31-pipeline-console-ui §4.1). No network."""
from __future__ import annotations

import json

from pipeline.nodes import notion_research as nr

CSV = "Title,Type,Category,Author/Host,Source URL\nA book,Book,Comprehensive Understanding,X,\nA talk,Video,Going Deeper,Y,\n"


def test_metric_row_has_page_ids_in_creation_order(tmp_path, monkeypatch):
    (tmp_path / "r.csv").write_text(CSV)
    (tmp_path / "entry.md").write_text("## Story Anchor\nran up a hill\n")
    made = iter(["pg-1", "pg-2"])
    monkeypatch.setattr(nr, "create_research_task", lambda **kw: {"id": next(made)})
    monkeypatch.setattr(nr, "page_id", lambda page: page["id"])
    monkeypatch.setattr(nr, "fetch_page_blocks", lambda pid: [])
    monkeypatch.setattr(nr, "_resolve_agent", lambda theme: None)
    nr.node_notion_research({"research_csv_path": str(tmp_path / "r.csv"), "vault_entry_path": str(tmp_path / "entry.md"),
                             "vault_entry_slug": "e", "run_dir": str(tmp_path)})
    rows = [json.loads(line) for line in (tmp_path / "runs.jsonl").read_text().splitlines()]
    row = next(r for r in rows if r["stage"] == "notion-research")
    assert row["page_ids"] == ["pg-1", "pg-2"] and row["created"] == 2
