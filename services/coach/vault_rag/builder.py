"""Load the Obsidian vault and bridge wikilinks into node relationships."""
from __future__ import annotations
from pathlib import Path
import re

from llama_index.core import PropertyGraphIndex, StorageContext, load_index_from_storage
from llama_index.core.graph_stores.simple_labelled import SimplePropertyGraphStore
from llama_index.core.indices.property_graph import ImplicitPathExtractor
from llama_index.core.schema import TextNode, NodeRelationship, RelatedNodeInfo
from llama_index.embeddings.openai import OpenAIEmbedding
from llama_index.readers.obsidian import ObsidianReader

WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)(?:\|[^\]]+)?\]\]")


def _slug(path: Path) -> str:
    return path.stem.lower()


def load_vault_documents(vault_dir: Path) -> list[TextNode]:
    reader = ObsidianReader(input_dir=str(vault_dir), extract_tasks=False)
    documents = reader.load_data()

    # Build a slug → node_id index for bridging wikilinks
    nodes: list[TextNode] = []
    for doc in documents:
        rel_path = Path(doc.metadata.get("file_path", doc.metadata.get("file_name", "unknown")))
        slug = _slug(rel_path)
        node = TextNode(
            text=doc.text,
            metadata={**doc.metadata, "slug": slug},
            id_=slug,
        )
        wikilinks = WIKILINK_RE.findall(doc.text)
        node.metadata["wikilinks"] = wikilinks
        nodes.append(node)

    slug_to_id = {n.metadata["slug"]: n.node_id for n in nodes}

    for node in nodes:
        for target_link in node.metadata["wikilinks"]:
            target_slug = target_link.split("/")[-1].strip().lower()
            target_id = slug_to_id.get(target_slug)
            if not target_id or target_id == node.node_id:
                continue
            node.relationships.setdefault(NodeRelationship.NEXT, [])
            related_info = RelatedNodeInfo(node_id=target_id, metadata={"name": target_slug})
            if isinstance(node.relationships[NodeRelationship.NEXT], list):
                node.relationships[NodeRelationship.NEXT].append(related_info)
            else:
                node.relationships[NodeRelationship.NEXT] = [
                    node.relationships[NodeRelationship.NEXT],
                    related_info,
                ]
    return nodes


def _embed_model():
    return OpenAIEmbedding(model="text-embedding-3-small")


def build_index(vault_dir: Path, storage_dir: Path) -> PropertyGraphIndex:
    nodes = load_vault_documents(vault_dir)
    graph_store = SimplePropertyGraphStore()
    storage_context = StorageContext.from_defaults(property_graph_store=graph_store)
    index = PropertyGraphIndex(
        nodes=nodes,
        storage_context=storage_context,
        embed_model=_embed_model(),
        kg_extractors=[ImplicitPathExtractor()],
        show_progress=False,
    )
    storage_context.persist(persist_dir=str(storage_dir))
    return index


def load_index(storage_dir: Path) -> PropertyGraphIndex:
    storage_context = StorageContext.from_defaults(persist_dir=str(storage_dir))
    return load_index_from_storage(storage_context, embed_model=_embed_model())
