"""promote_to_notion sends one task per inbox file with theme suggestions in body."""
from __future__ import annotations
from pathlib import Path
from sidecar.promote_to_notion import promote


def test_promote_creates_one_notion_task_per_file(tmp_path, monkeypatch):
    inbox = tmp_path / "_inbox" / "42"
    inbox.mkdir(parents=True)
    (inbox / "20260101T100000Z.md").write_text("---\nuser_id: 42\n---\n## User\nfoo\n## Coach\nbar")

    calls: list[dict] = []
    def fake_create_page(**kwargs):
        calls.append(kwargs)
        return {"id": "page_abc"}
    monkeypatch.setattr("sidecar.promote_to_notion._notion_create_page", fake_create_page)
    monkeypatch.setattr("sidecar.promote_to_notion._classify_top", lambda text: ["comfort-as-default"])

    promoted = promote(inbox_root=tmp_path / "_inbox", data_source_id="ds_1")
    assert len(promoted) == 1
    assert len(calls) == 1
    assert "comfort-as-default" in str(calls[0])
