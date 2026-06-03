"""Load the Obsidian vault and bridge wikilinks into node relationships."""
from __future__ import annotations
from pathlib import Path
import re

from llama_index.core import PropertyGraphIndex, StorageContext, load_index_from_storage
from llama_index.core.graph_stores.simple_labelled import SimplePropertyGraphStore
from llama_index.core.indices.property_graph import ImplicitPathExtractor
from llama_index.core.node_parser import SentenceSplitter
from llama_index.core.schema import TextNode, NodeRelationship, RelatedNodeInfo
from llama_index.embeddings.openai import OpenAIEmbedding
from llama_index.readers.obsidian import ObsidianReader

WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)(?:\|[^\]]+)?\]\]")


def _slug(path: Path) -> str:
    return path.stem.lower()


def load_vault_documents(vault_dir: Path) -> list[TextNode]:
    reader = ObsidianReader(input_dir=str(vault_dir), extract_tasks=False)
    documents = reader.load_data()
    # Chunk each file: a whole note can exceed the embedding model's 8192-token
    # input limit, and chunk-level nodes give finer-grained retrieval anyway.
    splitter = SentenceSplitter(chunk_size=512, chunk_overlap=64)

    nodes: list[TextNode] = []
    file_chunks: dict[str, list[TextNode]] = {}
    file_links: dict[str, list[str]] = {}
    for doc in documents:
        rel_path = Path(doc.metadata.get("file_path", doc.metadata.get("file_name", "unknown")))
        slug = _slug(rel_path)
        # Embed the chunk *text* only. ObsidianReader attaches large metadata
        # (paths, frontmatter) that can exceed the chunk size — the metadata-aware
        # splitter errors on it — and embedding it just dilutes the vector.
        doc.excluded_embed_metadata_keys = list(doc.metadata.keys())
        doc.excluded_llm_metadata_keys = list(doc.metadata.keys())
        chunks = splitter.get_nodes_from_documents([doc])
        for chunk in chunks:
            chunk.metadata["slug"] = slug
        file_chunks.setdefault(slug, []).extend(chunks)
        # ObsidianReader can emit several Documents per file, so accumulate the
        # wikilinks across all of them rather than per partial-document.
        file_links.setdefault(slug, []).extend(WIKILINK_RE.findall(doc.text))
        nodes.extend(chunks)

    # Dedupe each file's links (order-preserving) and stamp the full set on every
    # chunk, so any retrieved chunk carries its file's outbound links.
    for slug, chunks in file_chunks.items():
        links = list(dict.fromkeys(file_links.get(slug, [])))
        file_links[slug] = links
        for chunk in chunks:
            chunk.metadata["wikilinks"] = links

    # Each file is represented in the graph by its head chunk; wikilinks resolve
    # to the target file's head chunk.
    slug_to_head = {slug: chs[0].node_id for slug, chs in file_chunks.items() if chs}

    # Bridge wikilinks into CHILD relationships. CHILD is the list-typed slot;
    # NEXT must be a *single* RelatedNodeInfo (node.next_node raises on a list)
    # and ImplicitPathExtractor reads node.next_node, so multiple links must not
    # go under NEXT. The file's outbound links are attached to *every* chunk, so
    # any retrieved chunk can reach the notes its file links to.
    for slug, chunks in file_chunks.items():
        seen: set[str] = set()
        children: list[RelatedNodeInfo] = []
        for target_link in file_links.get(slug, []):
            target_slug = target_link.split("/")[-1].strip().lower()
            if target_slug == slug:
                continue
            target_id = slug_to_head.get(target_slug)
            if not target_id or target_id in seen:
                continue
            seen.add(target_id)
            children.append(RelatedNodeInfo(node_id=target_id, metadata={"name": target_slug}))
        if children:
            for chunk in chunks:
                chunk.relationships[NodeRelationship.CHILD] = list(children)

    # Keep embeddings text-only: exclude every metadata key (incl. the slug +
    # wikilinks added above) from the embed/LLM views of each node.
    for node in nodes:
        keys = list(node.metadata.keys())
        node.excluded_embed_metadata_keys = keys
        node.excluded_llm_metadata_keys = keys
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
    # Pass the same structural-only extractor used at build time. Without it,
    # PropertyGraphIndex reconstructs its default kg_extractors, which include
    # SimpleLLMPathExtractor → resolves Settings.llm → requires
    # llama-index-llms-openai. We don't use an LLM for graph extraction.
    return load_index_from_storage(
        storage_context,
        embed_model=_embed_model(),
        kg_extractors=[ImplicitPathExtractor()],
    )
