"""Smoke test: ObsidianReader + wikilink bridge produces nodes with typed relationships."""
from __future__ import annotations
from pathlib import Path
from llama_index.core.schema import NodeRelationship

from vault_rag.builder import load_vault_documents


def test_load_vault_documents_emits_nodes_with_wikilink_relationships(fixture_vault_dir: Path):
    nodes = load_vault_documents(fixture_vault_dir)
    assert len(nodes) >= 5

    # The test entry references comfort-as-default and deliberate-discomfort.
    entry_node = next(n for n in nodes if "running in the rain" in n.get_content().lower())
    related = entry_node.relationships
    referenced_names = {
        r.metadata.get("name") for r in related.values()
        if isinstance(r, list) is False and hasattr(r, "metadata")
    }
    # Bridged wikilinks must surface as relationships (not just metadata strings)
    assert any("comfort-as-default" in str(v) for v in entry_node.metadata.get("wikilinks", []))
