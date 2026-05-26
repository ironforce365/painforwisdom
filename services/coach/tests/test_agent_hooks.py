"""post_turn_hook writes JSONL entry to vault/_inbox/<user>/."""
from __future__ import annotations
from pathlib import Path
from agent.hooks import write_inbox_entry


def test_writes_inbox_entry(tmp_path: Path):
    write_inbox_entry(
        inbox_root=tmp_path,
        user_id="42",
        user_text="why do I always skip runs in winter?",
        assistant_text="What did you avoid this week, specifically?",
        retrieved_sources=["comfort-as-default", "2026-01-01-test-entry"],
    )
    files = list((tmp_path / "42").glob("*.md"))
    assert len(files) == 1
    body = files[0].read_text()
    assert "skip runs in winter" in body
    assert "comfort-as-default" in body
