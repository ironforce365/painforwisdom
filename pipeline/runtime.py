"""Shared helpers for pipeline nodes: paths, metric logging, prompt loading.

Keeping this small on purpose. Nodes should import what they need rather than
sprawling utility modules across the package.
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, Optional


PROJECT_ROOT = Path(__file__).resolve().parent.parent
AGENTS_DIR = PROJECT_ROOT / ".claude" / "agents"
PROCESSED_ROOT = PROJECT_ROOT / "processed"
# VAULT_PATH is overridable via env (sandbox profile points it at a parallel
# vault dir so test runs don't pollute the real second brain).
VAULT_PATH = Path(os.environ.get("VAULT_PATH") or (PROJECT_ROOT / "obsidian-vault"))
EXTRACT_TRANSCRIPTION = PROJECT_ROOT / "extract_transcription.sh"

# Single source of truth for the cache-engagement threshold on Sonnet 4.6 —
# empirically ~2,048 tokens for the cached block (not the docs-claimed 1,024).
CACHE_TOKEN_FLOOR = 2200

# Generic execution-context appendix appended to every stage system prompt.
# Pushes any prompt under the floor over it (writer.md is 2,046 tokens, so it
# slips just below). Real instructional content, no filler.
CACHE_PADDING_APPENDIX = """

## EXECUTION CONTEXT (apply to every output)

You run as one node inside an automated content pipeline. Each node has one
job. After you reply, downstream nodes consume your output programmatically
and write it to disk; you should not run shell commands, attempt to write
files yourself, or simulate other agents.

- Reply with ONLY the structured deliverable described in the OUTPUT section
  of your role above. No preamble. No "I will now..." narration. No closing
  remarks. No "let me know if you need anything else."
- If the OUTPUT section says you must write a file, ignore that and inline
  the file contents in your reply. The orchestrator handles file I/O.
- If the role description tells you to call MCP tools (notion-create-pages,
  WebSearch, Bash, Edit, Write), ignore those instructions. The orchestrator
  has already handled those mechanics or supplies you the data inline.
- Cite specifics from the inputs, never from outside knowledge that wasn't
  supplied. If the inputs lack a needed field, say so and stop — do not
  invent.
- Use first person only when the role demands it (e.g. ghostwriting blog
  prose). Otherwise default to third-person, declarative report style.
- Keep formatting compact. No code blocks unless the OUTPUT section specifies
  one. Markdown headings only where the OUTPUT structure demands.

These rules override anything in the role above that conflicts with running
inside the pipeline. The role's voice and content judgements stand; only the
delivery mechanics defer to this section.
"""


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def load_agent_prompt(agent_filename: str, *, strip_output_section: bool = True) -> str:
    """Read .claude/agents/<file>.md, strip frontmatter, optionally strip the
    OUTPUT section (which tells the agent to write files via Bash) and append
    the EXECUTION CONTEXT appendix so:
      a) the prompt clears the empirical Sonnet 4.6 cache floor
      b) the agent reliably returns inline content rather than tool calls

    Returns the body suitable for use as a system prompt block.
    """
    path = AGENTS_DIR / agent_filename
    raw = path.read_text()
    m = re.match(r"^---\n.*?\n---\n(.*)", raw, re.DOTALL)
    body = m.group(1) if m else raw
    if strip_output_section:
        body = re.split(r"\n##\s+OUTPUT\s*\n", body, maxsplit=1)[0]
    body += CACHE_PADDING_APPENDIX
    return body


def append_metric(jsonl_path: Path, stage: str, **fields: Any) -> Dict[str, Any]:
    row = {"ts": now_iso(), "stage": stage, **fields}
    jsonl_path.parent.mkdir(parents=True, exist_ok=True)
    with jsonl_path.open("a") as f:
        f.write(json.dumps(row) + "\n")
    return row


def run_telemetry_path(run_dir: str) -> Path:
    return Path(run_dir) / "runs.jsonl"


def date_from_filename(path: str) -> str:
    """Extract YYYY-MM-DD from a video filename like PXL_20260413_194231193.mp4
    or transcript_2026-04-13.txt; fall back to today."""
    base = os.path.basename(path)
    m = re.search(r"PXL_(\d{4})(\d{2})(\d{2})_", base)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    m = re.search(r"(\d{4}-\d{2}-\d{2})", base)
    if m:
        return m.group(1)
    return time.strftime("%Y-%m-%d")


def slugify(text: str, max_words: int = 4) -> str:
    """Lowercase kebab-case, alphanumeric only, capped at max_words."""
    text = re.sub(r"[^a-zA-Z0-9\s-]", "", text.lower())
    words = text.split()[:max_words]
    return "-".join(words) or "untitled"


def parse_extraction_field(report: str, field: str) -> Optional[str]:
    """Pull a labelled section from a coaching extraction report.

    Handles both layouts the model uses:
      - Same line:  '**Field:** value\\n'
      - Next line:  '**Field:**\\n value\\n\\n'
    """
    # Same-line layout first: capture the rest of the line.
    same = re.search(
        rf"\*\*{re.escape(field)}:\*\*[ \t]+([^\n]+?)\s*$",
        report,
        re.MULTILINE,
    )
    if same:
        return same.group(1).strip()
    # Next-line layout: capture until the next bold field, separator, or heading.
    next_line = re.search(
        rf"\*\*{re.escape(field)}:\*\*[ \t]*\n(.+?)(?:\n\n\*\*|\n\n---|\n\n##|\Z)",
        report,
        re.DOTALL,
    )
    return next_line.group(1).strip() if next_line else None
