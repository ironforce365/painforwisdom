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

    # Bridge: wikilinks become CHILD relationships (list-typed) pointing at
    # sibling nodes. CHILD — not NEXT — because NEXT must be a single
    # RelatedNodeInfo and ImplicitPathExtractor reads node.next_node.
    children = entry_node.relationships.get(NodeRelationship.CHILD)
    assert children is not None, "wikilink bridge produced no CHILD relationships"
    assert isinstance(children, list), "CHILD must be a list of RelatedNodeInfo"
    bridged_names = {n.metadata.get("name") for n in children}
    assert "comfort-as-default" in bridged_names
