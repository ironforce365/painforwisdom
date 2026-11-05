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
    DEEP_MODEL,
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
  # EXISTING theme file: append-style. Never send new_body for it.
  - slug: theme-slug-1
    entries_row: "| YYYY-MM-DD | [[YYYY-MM-DD-slug]] | one sentence |"
    key_insight: |
      ...optional: FULL new body of the "## Key insight so far" section...
    patterns_emerging: |
      ...optional: FULL new body of the "## Patterns emerging" section...
  # NEW theme (approved this run, no file yet): full body.
  - slug: new-theme-slug
    new_body: |
      # <Theme Name>
      ...full theme file body per the theme file format...
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

### Handling PRIOR APPROVAL EXCHANGE replies

When a `## PRIOR APPROVAL EXCHANGE` block is present in the user message,
Gonzalo's reply is NOT restricted to `yes` / `no` / `<slug>`. It may be a
free-form sentence with refinement requests. Interpret it like a thoughtful
collaborator:

- Plain `yes` (or clear approval w/ no changes) → emit PROCEED with the
  proposed slug, building entry/theme/framework bodies normally.
- Plain `no` (or clear rejection) → emit PROCEED reassigning to the closest
  existing theme/framework; do not request a new one.
- A bare kebab-case slug (e.g. `resistance-practice`) → emit PROCEED with
  that slug instead of the original.
- Anything else (a sentence, a question, a suggestion to broaden/narrow
  scope, rename, reframe core tension, drop a constraint like "daily",
  etc.) → treat it as REFINEMENT FEEDBACK. Emit another
  `NEEDS_APPROVAL_THEME` / `NEEDS_APPROVAL_FRAMEWORK` with the proposal
  updated to reflect the feedback (revised slug if the rename is implied,
  revised `reason`, revised `core_tension` or `definition`). Do not assume
  approval — Gonzalo will confirm or keep iterating on the next turn.
- If Gonzalo asks a clarifying question, the refined re-proposal should
  answer it in the `reason` / `core_tension` (or `definition`) fields.

Never silently ignore refinement feedback. Never PROCEED with the original
proposal after a conversational reply that asked for changes — re-propose.

Rules:
- Use only theme/framework slugs that already exist UNLESS proposing a new one.
- Slugs are kebab-case lowercase, hyphen-separated.
- Pre-approved exception: `pattern-manifestation` is a permanent theme — if
  the entry is Flagged, attach `pattern-manifestation` and never request
  approval for it.
- Date in entry_slug and entry_body must match the input video_date.
- Entry body must be the FULL markdown for entries/YYYY-MM-DD-slug.md, ready
  to write byte-for-byte. Same for theme_updates[*].new_body (new themes
  only) and book_outline_body.
- Every theme in themes_attached gets a theme_updates item. For an existing
  theme give `entries_row` (one row for its `## Entries` table); the
  pipeline inserts it and sets `**Last updated:**` and `**Entry count:**`.
  `key_insight` / `patterns_emerging`, when given, REPLACE that whole
  section, so carry forward what the current body (in the snapshot) says.
- book_outline_body is the full current outline from the snapshot, updated.
  Omit it rather than send a shortened outline.
- For any free-text field that may contain colons, quotes, or special
  characters (`reason`, `core_tension`, `definition`, `curator_summary`),
  ALWAYS use a YAML block scalar (`|` or `>-`) — never a quoted single-line
  string. Block scalars handle punctuation safely.
"""


_MAX_TOKENS = 16000

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

    # Full bodies, never truncated: the plan updates themes and rewrites the outline from them.
    for t in themes:
        parts.append(f"\nthemes/{t}.md (current):\n```\n{(themes_dir / f'{t}.md').read_text()}\n```")

    if book_outline.is_file():
        parts.append(f"\nbook-outline.md (current):\n```\n{book_outline.read_text()}\n```")

    return "\n".join(parts)


def _parse_plan(text: str, *, diag: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    m = _PLAN_PATTERN.search(text)
    if not m:
        diag_str = ""
        if diag:
            diag_str = (
                f"\n[diag] stop_reason={diag.get('stop_reason')!r} "
                f"output_tokens={diag.get('output_tokens')} "
                f"text_chars={len(text)} "
                f"opener_count={text.count('---kb-plan---')}"
            )
        raise RuntimeError(
            "kb-curator: response missing ---kb-plan--- markers."
            + diag_str
            + "\nGot (tail 800 chars):\n" + text[-800:]
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
    model = os.environ.get("PIPELINE_MODEL", DEEP_MODEL)
    result = call_llm(model, system_prompt, user_msg, max_tokens=_MAX_TOKENS)
    try:
        plan = _parse_plan(result["text"], diag=result)
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
        result2 = call_llm(model, system_prompt, retry_msg, max_tokens=_MAX_TOKENS)
        plan = _parse_plan(result2["text"], diag=result2)
        # Sum cost / tokens conservatively across both calls
        for k in ("input_tokens", "output_tokens", "cache_read_tokens", "cache_creation_tokens", "cost_usd", "duration_s"):
            result2[k] = (result.get(k, 0) or 0) + (result2.get(k, 0) or 0)
        return plan, result2


def _replace_section(body: str, heading: str, text: str) -> str:
    """Replace the body of `## <heading>` up to the next `## ` heading; append the section if absent."""
    m = re.search(rf"^## {re.escape(heading)}[ \t]*\n(.*?)(?=^## |\Z)", body, re.DOTALL | re.MULTILINE)
    new = f"## {heading}\n{text.strip()}\n"
    if not m:
        return body.rstrip("\n") + f"\n\n{new}"
    tail = "\n" if m.end() < len(body) else ""
    return body[:m.start()] + new + tail + body[m.end():]


def _append_theme(body: str, upd: Dict[str, Any], date: str) -> str:
    """Append-style update of an existing theme file (wo-60b5fec1): insert the entries row, refresh header fields."""
    lines = body.split("\n")
    try:
        head = next(i for i, l in enumerate(lines) if l.strip() == "## Entries")
    except StopIteration:
        raise RuntimeError("no '## Entries' section")
    start = head + 1
    while start < len(lines) and not lines[start].strip():
        start += 1
    end = start
    while end < len(lines) and lines[end].lstrip().startswith("|"):
        end += 1
    if end - start < 2:  # header + separator at minimum
        raise RuntimeError("no table under '## Entries'")
    lines.insert(end, upd["entries_row"].strip())
    body = "\n".join(lines)
    # Rows after insert: table lines minus header and separator, plus the new one.
    body = re.sub(r"^\*\*Entry count:\*\*.*$", f"**Entry count:** {end - start - 1}", body, flags=re.MULTILINE)
    if date:
        body = re.sub(r"^\*\*Last updated:\*\*.*$", f"**Last updated:** {date}", body, flags=re.MULTILINE)
    if isinstance(upd.get("key_insight"), str):
        body = _replace_section(body, "Key insight so far", upd["key_insight"])
    if isinstance(upd.get("patterns_emerging"), str):
        body = _replace_section(body, "Patterns emerging", upd["patterns_emerging"])
    return body


def _validate_plan(plan: Dict[str, Any], date: str = "") -> Dict[Path, str]:
    """Raise before any vault write, so a bad plan never leaves a half-written entry behind.

    Returns the theme file bodies to write.
    """
    bad = [k for k in ("entry_slug", "entry_body", "timeline_row") if not isinstance(plan.get(k), str)]
    bad += [f"framework_updates[{i}]" for i, u in enumerate(plan.get("framework_updates") or [])
            if not (isinstance(u, dict) and isinstance(u.get("slug"), str) and isinstance(u.get("new_body"), str))]
    theme_writes: Dict[Path, str] = {}
    for i, u in enumerate(plan.get("theme_updates") or []):
        name = f"theme_updates[{i}]"
        if not (isinstance(u, dict) and isinstance(u.get("slug"), str)):
            bad.append(name)
            continue
        path = VAULT_ROOT / "themes" / f"{u['slug']}.md"
        if not path.is_file():
            if isinstance(u.get("new_body"), str):
                theme_writes[path] = u["new_body"]
            else:
                bad.append(f"{name}.new_body (new theme)")
        elif "new_body" in u:
            bad.append(f"{name}.new_body not allowed for existing theme {u['slug']}")
        elif not isinstance(u.get("entries_row"), str):
            bad.append(f"{name}.entries_row")
        else:
            try:
                theme_writes[path] = _append_theme(path.read_text(), u, date)
            except RuntimeError as exc:
                bad.append(f"{name} ({u['slug']}: {exc})")
    if bad:
        raise RuntimeError(f"kb-curator: invalid plan, missing or non-string: {', '.join(bad)}")
    return theme_writes


def _apply_proceed(state: State, plan: Dict[str, Any]) -> Dict[str, Any]:
    """Write all vault files described by a PROCEED plan."""
    theme_writes = _validate_plan(plan, str(state.get("video_date", "") or ""))
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

    for tpath, tbody in theme_writes.items():
        tpath.write_text(tbody)

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
    outline = plan.get("book_outline_body")
    if isinstance(outline, str):
        book_outline_file.write_text(outline)
    else:
        print("[kb-curator] plan has no book_outline_body; book-outline.md left unchanged")

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


_RAW_PLAN_CAP = 2000
_REPLY_HINT = (
    "Reply freely. Examples:\n"
    "  • `yes` — approve as-is\n"
    "  • `no` — skip and attach to closest existing item\n"
    "  • `<alternative-slug>` — rename it\n"
    "  • any sentence with refinements (scope, framing, core tension,\n"
    "    naming, what to drop/add) — the curator will re-propose with\n"
    "    your feedback applied, and pause again for you to confirm."
)


def _proposal_text(plan: Dict[str, Any]) -> str:
    if plan["action"] == "NEEDS_APPROVAL_THEME":
        return (
            "⚠️ NEW THEME — approval required\n"
            f"Proposed: {plan.get('proposed_theme')}\n"
            f"Reason: {plan.get('reason')}\n"
            f"Core tension: {plan.get('core_tension')}\n\n"
            f"{_REPLY_HINT}"
        )
    return (
        "⚠️ NEW FRAMEWORK — approval required\n"
        f"Proposed: {plan.get('proposed_framework')}\n"
        f"Reason: {plan.get('reason')}\n"
        f"Definition: {plan.get('definition')}\n\n"
        f"{_REPLY_HINT}"
    )


def _unknown_action_text(plan: Dict[str, Any]) -> str:
    raw = yaml.safe_dump(plan, sort_keys=False, allow_unicode=True)
    return (
        f"⚠️ CURATOR — unknown action `{plan.get('action')}`\n"
        f"Reason: {plan.get('reason')}\n\n"
        f"Plan:\n{raw[:_RAW_PLAN_CAP]}\n\n"
        "Reply with what the curator should do: your answer goes back to it and it plans again."
    )


def _interrupt_payload(plan: Dict[str, Any]) -> Dict[str, Any]:
    action = plan.get("action")
    if not (isinstance(action, str) and action.startswith("NEEDS_APPROVAL")):
        return {"kind": "curator", "proposal": _unknown_action_text(plan), "raw_plan": plan}
    return {"kind": "theme" if action == "NEEDS_APPROVAL_THEME" else "framework", "proposal": _proposal_text(plan),
            "raw_plan": plan}


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

    # Any action but PROCEED asks: an unknown one (ALREADY_PROCESSED) must not crash a run that a retry would repeat.
    while plan.get("action") != "PROCEED":
        payload = _interrupt_payload(plan)
        proposal_msg = payload["proposal"]
        print(f"[kb-curator] suspending for approval:\n{proposal_msg}")
        # interrupt() suspends the graph; orchestrator handles the human round-trip
        # and resumes with Command(resume=<reply text>).
        reply: Optional[str] = interrupt(payload)
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
