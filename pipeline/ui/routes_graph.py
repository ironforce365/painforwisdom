"""GET /api/graph from the compiled graph, so topology lives only in pipeline/graph.py."""
from __future__ import annotations

from fastapi import APIRouter, Request

from pipeline.ui.mapping import NODE_ICON

router = APIRouter()
_HIDDEN = {"__start__", "__end__"}


def graph_payload() -> dict:
    from pipeline.graph import build_graph  # heavy: imports every node module

    compiled, saver = build_graph(start_at="transcribe")
    saver.conn.close()
    g = compiled.get_graph()
    nodes = [{"id": n, "label": n.replace("_", " "), "icon": NODE_ICON.get(n, "dot")} for n in g.nodes if n not in _HIDDEN]
    edges = [{"from": e.source, "to": e.target} for e in g.edges if e.source not in _HIDDEN and e.target not in _HIDDEN]
    return {"nodes": nodes, "edges": edges}


@router.get("/api/graph")
def get_graph(request: Request) -> dict:
    return request.app.state.graph
