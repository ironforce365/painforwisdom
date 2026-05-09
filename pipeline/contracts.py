"""Per-node input contracts.

Every pipeline node calls `assert_inputs("<node>", state)` as its first line.
On a missing/empty required field, raise `InputContractError` so the
orchestrator can classify it as a persistent failure (no point retrying:
upstream stage produced nothing useful) and escalate to the user via
Telegram.

This replaces a mix of direct `state["x"]` access (KeyError on miss, no
useful message) and `state.get("x", default)` (silent default, problem
surfaces three stages later as something cryptic).
"""
from __future__ import annotations

from typing import Dict, List, Mapping


class InputContractError(RuntimeError):
    """A node was invoked with state that did not satisfy its input contract."""

    def __init__(self, node: str, missing: List[str]) -> None:
        self.node = node
        self.missing = missing
        super().__init__(
            f"input contract violation in node {node!r}: missing/empty fields {missing}"
        )


# Required state keys per node. A key is "satisfied" when state.get(key)
# is truthy (non-empty string, non-empty list, non-zero number, etc.).
NODE_INPUTS: Dict[str, List[str]] = {
    "transcribe":      ["video_path", "run_dir"],
    "extract":         ["transcript_text", "transcript_path", "run_dir"],
    "kb_curator":      ["extraction_report", "video_date", "run_dir"],
    "writer":          ["extraction_report", "transcript_text", "video_date", "run_dir"],
    "research":        ["extraction_report", "vault_entry_slug", "run_dir"],
    "notion_blog":     ["blog_post_title", "blog_post_text", "video_date", "run_dir"],
    "notion_research": ["research_csv_path", "vault_entry_path", "vault_entry_slug", "run_dir"],
    "validator":       [],
}


def assert_inputs(node_name: str, state: Mapping[str, object]) -> None:
    """Raise InputContractError if any required field for `node_name` is
    missing or falsy in `state`.
    """
    if node_name not in NODE_INPUTS:
        raise KeyError(f"unknown node {node_name!r}; add it to NODE_INPUTS")
    missing = [k for k in NODE_INPUTS[node_name] if not state.get(k)]
    if missing:
        raise InputContractError(node=node_name, missing=missing)
