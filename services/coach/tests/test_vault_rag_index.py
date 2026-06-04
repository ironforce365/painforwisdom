"""Index build + persist + reload round-trip."""
from __future__ import annotations
from pathlib import Path
import pytest

from vault_rag.builder import build_index, load_index


@pytest.mark.skipif(
    not __import__("os").environ.get("OPENAI_API_KEY"),
    reason="needs OPENAI_API_KEY for embeddings",
)
def test_build_and_reload_index(fixture_vault_dir: Path, tmp_index_dir: Path):
    idx = build_index(fixture_vault_dir, tmp_index_dir)
    assert (tmp_index_dir / "graph_store.json").exists()
    assert (tmp_index_dir / "docstore.json").exists()

    reloaded = load_index(tmp_index_dir)
    assert reloaded is not None
    response = reloaded.as_query_engine(similarity_top_k=2).query("rain run")
    assert "rain" in str(response).lower() or "comfort" in str(response).lower()
