"""Hybrid retrieval: vector + BM25 → reciprocal rank fusion → cross-encoder rerank."""
from __future__ import annotations
from llama_index.core import PropertyGraphIndex
from llama_index.core.retrievers import QueryFusionRetriever
from llama_index.retrievers.bm25 import BM25Retriever
from llama_index.core.postprocessor import SentenceTransformerRerank
from llama_index.core.base.base_retriever import BaseRetriever


class _RerankRetriever(BaseRetriever):
    def __init__(self, inner: BaseRetriever, top_n: int):
        super().__init__()
        self._inner = inner
        self._rerank = SentenceTransformerRerank(
            model="cross-encoder/ms-marco-MiniLM-L-6-v2",
            top_n=top_n,
        )

    def _retrieve(self, query_bundle):
        nodes = self._inner.retrieve(query_bundle)
        return self._rerank.postprocess_nodes(nodes, query_bundle)


def build_retriever(index: PropertyGraphIndex, top_k: int = 5) -> BaseRetriever:
    pg_retriever = index.as_retriever(similarity_top_k=top_k * 2)
    docstore_nodes = list(index.docstore.docs.values())
    bm25_retriever = BM25Retriever.from_defaults(
        nodes=docstore_nodes,
        similarity_top_k=top_k * 2,
    )
    fusion = QueryFusionRetriever(
        retrievers=[pg_retriever, bm25_retriever],
        similarity_top_k=top_k * 2,
        mode="reciprocal_rerank",
        use_async=False,
        verbose=False,
    )
    return _RerankRetriever(fusion, top_n=top_k)
