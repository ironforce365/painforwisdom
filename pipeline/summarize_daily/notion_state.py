"""Update Notion Research Tasks rows after a brief is written.

Sets Status='Summarized' and writes the cluster-dir relative path into
`Summary Doc`. Tries to be safe across partial failures — each row gets its
own try/except; failures are returned for caller logging.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Dict, List

from pipeline.notion_client import get_client
from pipeline.runtime import PROJECT_ROOT


NOTION_PACING_S = 0.5


def mark_summarized(
    rows: List[Dict[str, Any]],
    cluster_dir: Path,
    notebooklm_url: str = "",
) -> tuple[int, List[Dict[str, str]]]:
    """Returns (ok_count, errors). errors = [{'page_id': ..., 'error': ...}]."""
    client = get_client()
    rel = cluster_dir.relative_to(PROJECT_ROOT)
    errors: List[Dict[str, str]] = []
    ok = 0
    for r in rows:
        try:
            props: Dict[str, Any] = {
                "Status": {"select": {"name": "Summarized"}},
                "Summary Doc": {"rich_text": [{"text": {"content": str(rel)[:1900]}}]},
            }
            if notebooklm_url:
                props["NotebookLM URL"] = {"rich_text": [{"text": {"content": notebooklm_url[:1900]}}]}
            client.pages.update(page_id=r["page_id"], properties=props)
            ok += 1
            time.sleep(NOTION_PACING_S)
        except Exception as exc:  # noqa: BLE001
            errors.append({"page_id": r.get("page_id", ""), "error": str(exc)[:300]})
    return ok, errors
