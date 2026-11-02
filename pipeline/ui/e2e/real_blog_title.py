"""Print a run's blog title straight from Notion, bypassing the console (spec 2026-10-31-pipeline-console-ui §10.3).

python pipeline/ui/e2e/real_blog_title.py <run_dir>
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from dotenv import dotenv_values

from pipeline import notion_client as nc
from pipeline.runtime import PROJECT_ROOT


def main(run_dir: Path) -> None:
    rows = [json.loads(line) for line in (run_dir / "runs.jsonl").read_text().splitlines() if line.strip()]
    page_id = next(r["page_id"] for r in reversed(rows) if r.get("stage") == "notion-blog" and r.get("page_id"))
    nc.get_client(token=dotenv_values(PROJECT_ROOT / ".env.sandbox")["NOTION_API_KEY"])
    print(nc.extract_property(nc.get_blog_page(str(page_id)), "Title"))


if __name__ == "__main__":
    main(Path(sys.argv[1]))
