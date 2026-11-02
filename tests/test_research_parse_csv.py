"""research._parse_csv on truncated model output (defect found by spec 2026-10-31-pipeline-console-ui §10 real runs)."""
from __future__ import annotations

import pytest

from pipeline.nodes.research import _parse_csv

HEADER = "Title,Type,Author/Host,Specific Location,Category,Research Angle,Relevance,Source URL,Paywall,Coaching Theme,Vault Entry,Reachable,Reachability Reason"
ROW = '"A paper","Paper","X","Abstract","Going Deeper","angle","why","https://example.org/a","false","t","e","yes",""'


def test_closed_fence():
    rows = _parse_csv(f"```csv\n{HEADER}\n{ROW}\n```\n")
    assert [r["Title"] for r in rows] == ["A paper"]


@pytest.mark.parametrize("tail", ['"Quantifying training inten', '"Quantifying","Paper","K. S. Seiler","Abstract: 318 sess'])
def test_unclosed_fence_drops_truncated_tail_row(tail):
    # stop_reason='length': no closing fence, last row cut short.
    rows = _parse_csv(f"```csv\n{HEADER}\n{ROW}\n{tail}")
    assert [r["Title"] for r in rows] == ["A paper"]
    assert rows[0]["Type"] == "Paper"


def test_title_less_rows_dropped_so_node_raises():
    assert _parse_csv(f"```csv\n{HEADER}\n" + ",".join(['""'] * 13) + "\n") == []
