"""Per-user scratchpad: write → read → list isolation across users."""
from __future__ import annotations
from pathlib import Path
import pytest

from user_memory.mcp_server import UserMemory


def test_per_user_isolation(tmp_path: Path):
    mem = UserMemory(base_dir=tmp_path)
    mem.create(user_id="111", path="notes.md", content="alice loves cold showers")
    mem.create(user_id="222", path="notes.md", content="bob hates running")

    assert mem.view(user_id="111", path="notes.md") == "alice loves cold showers"
    assert mem.view(user_id="222", path="notes.md") == "bob hates running"
    assert "notes.md" in mem.list(user_id="111")
    assert "notes.md" in mem.list(user_id="222")


def test_path_traversal_rejected(tmp_path: Path):
    mem = UserMemory(base_dir=tmp_path)
    with pytest.raises(ValueError):
        mem.create(user_id="111", path="../escape.md", content="x")
    with pytest.raises(ValueError):
        mem.view(user_id="111", path="/etc/passwd")
