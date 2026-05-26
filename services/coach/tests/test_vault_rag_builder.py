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

    # Raw wikilinks captured in metadata
    assert any("comfort-as-default" in str(v) for v in entry_node.metadata.get("wikilinks", []))

    # Bridge: wikilinks become typed NEXT relationships pointing at sibling nodes
    nexts = entry_node.relationships.get(NodeRelationship.NEXT)
    assert nexts is not None, "wikilink bridge produced no NEXT relationships"
    if not isinstance(nexts, list):
        nexts = [nexts]
    bridged_names = {n.metadata.get("name") for n in nexts}
    assert "comfort-as-default" in bridged_names
