"""Reranker load failure degrades to fusion-only retrieval, not a dead retriever.

Observed 2026-07-04: with container DNS down, SentenceTransformerRerank's cold
build tried a huggingface.co metadata check and raised — and the WHOLE doctrine
retriever build failed, so every turn ran without doctrine grounding. The
cross-encoder is an optional quality layer over vector+BM25 fusion; losing it
must cost ranking quality only, never retrieval itself.
"""
from __future__ import annotations

import vault_rag.retriever as vr


class _FakeInner:
    """Stands in for the fusion retriever."""


def test_maybe_rerank_wraps_when_reranker_loads(monkeypatch):
    class OkRerank:
        def __init__(self, *a, **kw):
            pass

    monkeypatch.setattr(vr, "SentenceTransformerRerank", OkRerank)
    inner = _FakeInner()
    out = vr._maybe_rerank(inner, top_n=5)
    assert isinstance(out, vr._RerankRetriever)


def test_maybe_rerank_falls_back_to_inner_on_load_failure(monkeypatch, caplog):
    class BoomRerank:
        def __init__(self, *a, **kw):
            raise RuntimeError("Cannot send a request, as the client has been closed.")

    monkeypatch.setattr(vr, "SentenceTransformerRerank", BoomRerank)
    inner = _FakeInner()
    with caplog.at_level("WARNING"):
        out = vr._maybe_rerank(inner, top_n=5)
    assert out is inner  # fusion-only, same object
    assert any("rerank" in r.message.lower() for r in caplog.records)
