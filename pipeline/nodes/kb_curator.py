"""Stage 3 — kb-curator with HITL interrupt for new theme / framework approval.

The legacy agent did its own file writes via Bash tool calls. In LangGraph
we keep file I/O in Python: the LLM returns a structured CURATION PLAN as
labelled YAML-ish blocks, and this node parses + applies it.

If the plan proposes a new theme or framework, we suspend the graph via
`interrupt(...)` so the orchestrator (run.py) can prompt Gonzalo via Telegram
and resume with `Command(resume=<reply>)`. We then re-prompt the LLM with the
approval reply baked in and apply the resulting plan.
"""
from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml
from langgraph.types import interrupt

from pipeline.contracts import assert_inputs
from pipeline.llm import call_llm
from pipeline.runtime import (
    VAULT_PATH,
    append_metric,
    load_agent_prompt,
    run_telemetry_path,
)
from pipeline.state import State


VAULT_ROOT = VAULT_PATH / "gonzalo-book"


_KB_OUTPUT_SPEC = """\

## OUTPUT SPEC (STRICT)

Reply with ONLY a YAML document framed by `---kb-plan---` markers. No prose,
no markdown headings outside the YAML. Choose ONE of:

A. PROCEED — when this entry fits existing themes/frameworks (no approval needed).
B. NEEDS_APPROVAL_THEME — when a new theme is required.
C. NEEDS_APPROVAL_FRAMEWORK — when a new framework is required.

If multiple new items are needed, request them ONE AT A TIME — surface only
the first new item. The orchestrator will resume the graph and you will be
re-invoked with the approval reply, at which point you can request the next
new item or PROCEED.

### A. PROCEED format

```
---kb-plan---
action: PROCEED
entry_slug: "<YYYY-MM-DD-2-to-4-word-kebab>"
themes_attached: ["theme-slug-1", "theme-slug-2"]
frameworks_attached: ["framework-slug-1"]
entry_body: |
  # YYYY-MM-DD — <Core insight as title>

  **Date:** YYYY-MM-DD
  **Content Quality:** Strong / Weak / Flagged
  **Themes:** [[theme-slug-1]], [[theme-slug-2]]
  **Frameworks:** [[framework-slug-1]]

  ## Core Insight
  ...

  ## Story Anchor
  ...

  (rest per the entry template — every section from the role description)

  ---
  *Entry created by kb-curator. Do not edit.*
timeline_row: "| YYYY-MM-DD | [[YYYY-MM-DD-slug]] | [[theme-1]], [[theme-2]] | Strong | one-sentence insight |"
theme_updates:
  - slug: theme-slug-1
    new_body: |
      # <Theme Name>
      ...full updated theme file body...
  - slug: theme-slug-2
    new_body: |
      ...
framework_updates: []
book_outline_body: |
  # Book Outline — In Formation

  ...full updated book-outline.md body, reflecting the new entry...
curator_summary: |
  ✓ Entry created: entries/YYYY-MM-DD-slug.md
  ✓ Themes updated: [[theme-1]], [[theme-2]]
  ✓ Timeline updated: _index.md
  ✓ Book outline updated: book-outline.md
---kb-plan---
```

### B. NEEDS_APPROVAL_THEME format

Use YAML block scalars (`>-`) for `reason` and `core_tension` so colons,
quotes, and other punctuation inside the prose don't break parsing.

```
---kb-plan---
action: NEEDS_APPROVAL_THEME
proposed_theme: theme-slug
reason: >-
  one sentence why no existing theme fits — colons, quotes, etc. are safe here
core_tension: >-
  the central question this theme addresses
---kb-plan---
```

### C. NEEDS_APPROVAL_FRAMEWORK format

```
---kb-plan---
action: NEEDS_APPROVAL_FRAMEWORK
proposed_framework: framework-slug
reason: >-
  one sentence why this deserves a framework file vs an existing one
definition: >-
  one paragraph description
---kb-plan---
```

Rules:
- Use only theme/framework slugs that already exist UNLESS proposing a new one.
- Slugs are kebab-case lowercase, hyphen-separated.
- Pre-approved exception: `pattern-manifestation` is a permanent theme — if
  the entry is Flagged, attach `pattern-manifestation` and never request
  approval for it.
- Date in entry_slug and entry_body must match the input video_date.
- Entry body must be the FULL markdown for entries/YYYY-MM-DD-slug.md, ready
  to write byte-for-byte. Same for theme_updates[*].new_body and
  book_outline_body.
- For any free-text field that may contain colons, quotes, or special
  characters (`reason`, `core_tension`, `definition`, `curator_summary`),
  ALWAYS use a YAML block scalar (`|` or `>-`) — never a quoted single-line
  string. Block scalars handle punctuation safely.
"""


_PLAN_PATTERN = re.compile(r"---kb-plan---\s*\n(.*?)\n---kb-plan---", re.DOTALL)


def _retry_prompt_for_format(text: str) -> str:
    return (
        "Your previous reply did not include the required `---kb-plan---` "
        "fenced YAML block. Reply again — ONLY the YAML inside markers, "
        "nothing else, no apology. Previous reply (for reference):\n\n"
        f"```\n{text[:2000]}\n```"
    )


def _retry_prompt_for_yaml(text: str, err: str) -> str:
    return (
        "Your previous reply was inside `---kb-plan---` markers but the YAML "
        "failed to parse. Most common cause: an unquoted free-text field "
        "(`reason`, `core_tension`, `definition`) contained a colon or "
        "another reserved character. Re-emit the SAME plan, but for every "
        "free-text field use a YAML block scalar like:\n\n"
        "    reason: >-\n"
        "      your prose here, colons and quotes are safe\n\n"
        "Reply again — ONLY the YAML inside `---kb-plan---` markers, no "
        "apology, no prose outside.\n\n"
        f"Parser error: {err}\n\n"
        f"Previous reply (for reference):\n```\n{text[:2000]}\n```"
    )


def _build_system_prompt() -> str:
    return load_agent_prompt("kb-curator.md") + _KB_OUTPUT_SPEC


def _vault_snapshot() -> str:
    """A compact summary of current vault state for the LLM."""
    themes_dir = VAULT_ROOT / "themes"
    frameworks_dir = VAULT_ROOT / "frameworks"
    entries_dir = VAULT_ROOT / "entries"
    index_file = VAULT_ROOT / "_index.md"
    book_outline = VAULT_ROOT / "book-outline.md"

    parts: List[str] = []
    parts.append(f"VAULT_ROOT: {VAULT_ROOT}")

    themes = sorted(p.stem for p in themes_dir.glob("*.md")) if themes_dir.is_dir() else []
    frameworks = sorted(p.stem for p in frameworks_dir.glob("*.md")) if frameworks_dir.is_dir() else []
    parts.append(f"\nExisting themes ({len(themes)}):\n" + "\n".join(f"  - {t}" for t in themes))
    parts.append(f"\nExisting frameworks ({len(frameworks)}):\n" + "\n".join(f"  - {f}" for f in frameworks))

    if entries_dir.is_dir():
        entries = sorted(p.stem for p in entries_dir.glob("*.md"))
        parts.append(f"\nExisting entries (last 10 of {len(entries)}):\n" + "\n".join(f"  - {e}" for e in entries[-10:]))

    if index_file.is_file():
        idx = index_file.read_text()
        idx_tail = "\n".join(idx.splitlines()[-15:])
        parts.append(f"\n_index.md (tail):\n```\n{idx_tail}\n```")

    if book_outline.is_file():
        parts.append(f"\nbook-outline.md (current):\n```\n{book_outline.read_text()[:3000]}\n```")

    return "\n".join(parts)


def _parse_plan(text: str) -> Dict[str, Any]:
    m = _PLAN_PATTERN.search(text)
    if not m:
        raise RuntimeError(
            "kb-curator: response missing ---kb-plan--- markers. Got:\n" + text[:600]
        )
    payload = m.group(1)
    try:
        return yaml.safe_load(payload)
    except yaml.YAMLError as exc:
        raise RuntimeError(f"kb-curator: invalid YAML in plan: {exc}\nPayload:\n{payload[:600]}")


def _user_message(state: State, *, approval_history: List[Tuple[str, str]]) -> str:
    extraction = state.get("extraction_report", "")
    snapshot = _vault_snapshot()
    history_block = ""
    if approval_history:
        lines = ["\n## PRIOR APPROVAL EXCHANGE"]
        for proposal, reply in approval_history:
            lines.append(f"- Proposal: {proposal}")
            lines.append(f"  Gonzalo's reply: {reply}")
        history_block = "\n".join(lines) + "\n"

    return (
        f"Video date: {state.get('video_date','')}\n"
        f"Run dir: {state.get('run_dir','')}\n"
        f"Vault path: {VAULT_PATH}\n\n"
        f"## CURRENT VAULT SNAPSHOT\n{snapshot}\n\n"
        f"## EXTRACTION REPORT\n```\n{extraction}\n```\n"
        f"{history_block}"
    )


def _call_llm_for_plan(state: State, approval_history: List[Tuple[str, str]]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Call the LLM, parse the plan. If output spec was violated (missing
    `---kb-plan---` markers), retry ONCE with a corrective user message.
    Sonnet sometimes drops the structured output on resume turns, especially
    when the prior turn's user message contains a rejected proposal.
    """
    system_prompt = _build_system_prompt()
    user_msg = _user_message(state, approval_history=approval_history)
    model = os.environ.get("PIPELINE_MODEL", "claude-sonnet-4-6")
    result = call_llm(model, system_prompt, user_msg, max_tokens=4000)
    try:
        plan = _parse_plan(result["text"])
        return plan, result
    except RuntimeError as exc:
        msg = str(exc)
        is_format = "---kb-plan---" in msg
        is_yaml = "invalid YAML" in msg
        if not (is_format or is_yaml):
            raise
        if is_format:
            print("[kb-curator] format violation, retrying once with corrective prompt")
            retry_msg = user_msg + "\n\n" + _retry_prompt_for_format(result["text"])
        else:
            print("[kb-curator] YAML parse error, retrying once with block-scalar guidance")
            retry_msg = user_msg + "\n\n" + _retry_prompt_for_yaml(result["text"], msg)
        result2 = call_llm(model, system_prompt, retry_msg, max_tokens=4000)
        plan = _parse_plan(result2["text"])
        # Sum cost / tokens conservatively across both calls
        for k in ("input_tokens", "output_tokens", "cache_read_tokens", "cache_creation_tokens", "cost_usd", "duration_s"):
            result2[k] = (result.get(k, 0) or 0) + (result2.get(k, 0) or 0)
        return plan, result2


def _apply_proceed(state: State, plan: Dict[str, Any]) -> Dict[str, Any]:
    """Write all vault files described by a PROCEED plan."""
    entries_dir = VAULT_ROOT / "entries"
    themes_dir = VAULT_ROOT / "themes"
    frameworks_dir = VAULT_ROOT / "frameworks"
    entries_dir.mkdir(parents=True, exist_ok=True)
    themes_dir.mkdir(parents=True, exist_ok=True)
    frameworks_dir.mkdir(parents=True, exist_ok=True)

    slug = plan["entry_slug"]
    entry_path = entries_dir / f"{slug}.md"
    if entry_path.exists():
        # Entries are immutable; refuse to overwrite.
        raise RuntimeError(f"kb-curator: entry already exists, refusing overwrite: {entry_path}")
    entry_path.write_text(plan["entry_body"])

    for tu in plan.get("theme_updates", []) or []:
        tpath = themes_dir / f"{tu['slug']}.md"
        tpath.write_text(tu["new_body"])

    for fu in plan.get("framework_updates", []) or []:
        fpath = frameworks_dir / f"{fu['slug']}.md"
        fpath.write_text(fu["new_body"])

    index_file = VAULT_ROOT / "_index.md"
    if not index_file.is_file():
        index_file.write_text(
            "# Gonzalo's Journey — Master Timeline\n\n"
            "| Date | Entry | Themes | Quality | Core Insight |\n"
            "|------|-------|--------|---------|--------------|\n"
        )
    with index_file.open("a") as f:
        f.write(plan["timeline_row"].rstrip() + "\n")

    book_outline_file = VAULT_ROOT / "book-outline.md"
    book_outline_file.write_text(plan["book_outline_body"])

    run_dir = Path(state["run_dir"])
    summary_dir = run_dir / "kb-curator"
    summary_dir.mkdir(parents=True, exist_ok=True)
    summary_path = summary_dir / "curator_summary.md"
    summary_path.write_text(plan.get("curator_summary", "✓ kb-curator completed"))

    return {
        "vault_entry_path": str(entry_path),
        "vault_entry_slug": slug,
        "curator_summary_path": str(summary_path),
        "themes_attached": list(plan.get("themes_attached") or []),
        "frameworks_attached": list(plan.get("frameworks_attached") or []),
        "pending_approval": None,
    }


def _proposal_text(plan: Dict[str, Any]) -> str:
    if plan["action"] == "NEEDS_APPROVAL_THEME":
        return (
            "⚠️ NEW THEME — approval required\n"
            f"Proposed: {plan.get('proposed_theme')}\n"
            f"Reason: {plan.get('reason')}\n"
            f"Core tension: {plan.get('core_tension')}\n\n"
            "Reply: yes / no / <alternative-slug>"
        )
    return (
        "⚠️ NEW FRAMEWORK — approval required\n"
        f"Proposed: {plan.get('proposed_framework')}\n"
        f"Reason: {plan.get('reason')}\n"
        f"Definition: {plan.get('definition')}\n\n"
        "Reply: yes / no / <alternative-slug>"
    )


def node_kb_curator(state: State) -> Dict[str, Any]:
    assert_inputs("kb_curator", state)
    t0 = time.time()
    print("[kb-curator] start")

    approval_history: List[Tuple[str, str]] = []
    metrics: List[Dict[str, Any]] = []

    plan, result = _call_llm_for_plan(state, approval_history)
    metrics.append(
        {
            "duration_s": round(result["duration_s"], 2),
            "input_tokens": result["input_tokens"],
            "output_tokens": result["output_tokens"],
            "cache_read_tokens": result["cache_read_tokens"],
            "cache_creation_tokens": result["cache_creation_tokens"],
            "cost_usd": result["cost_usd"],
            "phase": "initial",
        }
    )

    while plan.get("action", "").startswith("NEEDS_APPROVAL"):
        proposal_msg = _proposal_text(plan)
        print(f"[kb-curator] suspending for approval:\n{proposal_msg}")
        # interrupt() suspends the graph; orchestrator handles Telegram round-trip
        # and resumes with Command(resume=<reply text>).
        reply: Optional[str] = interrupt(
            {
                "kind": "theme" if plan["action"] == "NEEDS_APPROVAL_THEME" else "framework",
                "proposal": proposal_msg,
                "raw_plan": plan,
            }
        )
        reply = (reply or "").strip()
        print(f"[kb-curator] resumed with reply: {reply!r}")
        approval_history.append((proposal_msg, reply))
        plan, result = _call_llm_for_plan(state, approval_history)
        metrics.append(
            {
                "duration_s": round(result["duration_s"], 2),
                "input_tokens": result["input_tokens"],
                "output_tokens": result["output_tokens"],
                "cache_read_tokens": result["cache_read_tokens"],
                "cache_creation_tokens": result["cache_creation_tokens"],
                "cost_usd": result["cost_usd"],
                "phase": f"resume_{len(approval_history)}",
            }
        )

    if plan.get("action") != "PROCEED":
        raise RuntimeError(f"kb-curator: unexpected terminal action: {plan.get('action')}")

    out = _apply_proceed(state, plan)
    duration = time.time() - t0

    total_cost = sum(m["cost_usd"] for m in metrics)
    append_metric(
        run_telemetry_path(state["run_dir"]),
        "kb-curator",
        duration_s=round(duration, 2),
        llm_calls=len(metrics),
        approvals=len(approval_history),
        total_cost_usd=round(total_cost, 6),
        per_call=metrics,
    )
    print(
        f"[kb-curator] done {duration:.1f}s calls={len(metrics)} "
        f"approvals={len(approval_history)} entry={out['vault_entry_slug']}"
    )
    return out
