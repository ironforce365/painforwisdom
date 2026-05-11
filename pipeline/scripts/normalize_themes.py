"""Phase 3.5 — one-shot theme normalization + sub-theme split for Research Tasks.

Passes:

1. **Variant collapse** — hardcoded map of known spellings/compounds to canonical
   theme names. Idempotent; safe to re-run. Writes original value into the
   `Theme Audit Log` rich_text property so nothing is silently lost.

2. **Sub-theme split** — for each saturated canonical theme (≥30 rows), one
   Sonnet 4.6 call produces:
     a) 3-6 sub-themes with one-line definitions
     b) per-row sub-theme assignment
   Output written to `reports/sub-theme-proposal-<date>.md` for human review.
   On `--apply`, re-reads the proposal (which the operator may have edited) and
   updates each row's `Coaching Theme`.

Run order:
    python -m pipeline.scripts.normalize_themes --collapse --dry-run
    python -m pipeline.scripts.normalize_themes --collapse --apply
    python -m pipeline.scripts.normalize_themes --split --dry-run   # writes proposal
    python -m pipeline.scripts.normalize_themes --split --apply     # applies proposal
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any, Dict, List

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv

load_dotenv(PROJECT_ROOT / ".env")

from pipeline.llm import call_llm  # noqa: E402
from pipeline.notion_client import (  # noqa: E402
    extract_property,
    get_client,
    query_research_tasks,
)


SATURATION_THRESHOLD = 30
PROPOSAL_DIR = PROJECT_ROOT / "reports"


# Canonical theme set (per plan + emerging themes that earned themselves a place).
CANONICAL_THEMES = {
    "deliberate-discomfort",
    "comfort-as-default",
    "body-literacy",
    "naming-the-fear",
    "preparedness-debt",
    "strategic-vs-manufactured-suffering",
    "amcc-effect",
    # Newer earned-themes from 2026-04+ entries (kept because they have distinct
    # vault-entry anchors, not just terminology variants).
    "constraints-as-redirectors",
    "guilt-recovery",
    "discomfort-gap",
    "breathing-as-first-override",
    "mental-override",
}


# Compound + variant → canonical mapping. Compound rule: pick the more specific
# theme (the right side of the slash is typically less specific). Variant rule:
# pick the canonical kebab-case spelling.
VARIANT_MAP = {
    # Compounds — pick the more specific theme
    "amcc-effect / deliberate-discomfort": "amcc-effect",
    "comfort-as-default / deliberate-discomfort": "comfort-as-default",
    "deliberate-discomfort / body-literacy": "deliberate-discomfort",
    # Spelling / casing variants
    "Rest as Discipline": "rest-as-discipline",
    "rest as discipline": "rest-as-discipline",
    "strategic-discomfort": "strategic-vs-manufactured-suffering",
    "consistency-over-perfection": "consistency",
}

# One-off themes (≤2 rows, not in CANONICAL_THEMES, not in VARIANT_MAP). These
# get flagged for human review rather than auto-renamed.
ONE_OFF_FLAG_THRESHOLD = 2


def _classify(theme: str) -> str:
    """Return one of: canonical / variant / one-off / unknown."""
    if theme in CANONICAL_THEMES:
        return "canonical"
    if theme in VARIANT_MAP:
        return "variant"
    return "unknown"


def collect_rows() -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for page in query_research_tasks(page_size=100):
        rows.append(
            {
                "page_id": page.get("id", ""),
                "title": extract_property(page, "Title"),
                "coaching_theme": extract_property(page, "Coaching Theme") or "",
                "research_angle": extract_property(page, "Research Angle") or "",
                "relevance": extract_property(page, "Relevance") or "",
                "theme_audit_log": extract_property(page, "Theme Audit Log") or "",
            }
        )
    return rows


# ------------------------------- COLLAPSE PASS -------------------------------


def _plan_collapse(rows: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    """Returns {target_theme: [rows]} for rows that should be renamed."""
    by_theme = defaultdict(list)
    for r in rows:
        by_theme[r["coaching_theme"]].append(r)

    plan: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for theme, theme_rows in by_theme.items():
        if not theme:
            continue
        kind = _classify(theme)
        if kind == "variant":
            target = VARIANT_MAP[theme]
            plan[target].extend(theme_rows)
        elif kind == "unknown" and len(theme_rows) <= ONE_OFF_FLAG_THRESHOLD:
            # Flag, don't auto-rename — human reviews
            pass
        elif kind == "unknown" and len(theme_rows) > ONE_OFF_FLAG_THRESHOLD:
            # Larger unknown theme — leave as-is, it's earned its own slot
            pass
    return dict(plan)


def _apply_collapse(plan: Dict[str, List[Dict[str, Any]]]) -> int:
    client = get_client()
    n = 0
    for target_theme, theme_rows in plan.items():
        for r in theme_rows:
            old_theme = r["coaching_theme"]
            audit_log = r["theme_audit_log"]
            audit_entry = f"{date.today().isoformat()}: collapsed '{old_theme}' -> '{target_theme}'"
            full_audit = (audit_log + " | " + audit_entry) if audit_log else audit_entry
            client.pages.update(
                page_id=r["page_id"],
                properties={
                    "Coaching Theme": {"rich_text": [{"text": {"content": target_theme}}]},
                    "Theme Audit Log": {"rich_text": [{"text": {"content": full_audit[:1900]}}]},
                },
            )
            time.sleep(0.4)
            n += 1
    return n


def cmd_collapse(args: argparse.Namespace) -> int:
    rows = collect_rows()
    plan = _plan_collapse(rows)
    print(f"Rows scanned: {len(rows)}")
    if not plan:
        print("No collapse changes planned. Schema is canonical or already-normalised.")
        return 0
    n_rows = sum(len(v) for v in plan.values())
    print(f"Planned collapses: {len(plan)} target themes, {n_rows} rows.\n")
    for target_theme, theme_rows in plan.items():
        by_source = defaultdict(int)
        for r in theme_rows:
            by_source[r["coaching_theme"]] += 1
        print(f"  -> {target_theme}")
        for src, count in by_source.items():
            print(f"        {count:3d} rows from `{src}`")

    # One-offs (≤ THRESHOLD rows, unknown theme) listed for human review
    by_theme = defaultdict(int)
    for r in rows:
        by_theme[r["coaching_theme"]] += 1
    one_offs = [
        (t, n) for t, n in by_theme.items()
        if t and _classify(t) == "unknown" and n <= ONE_OFF_FLAG_THRESHOLD
    ]
    if one_offs:
        print("\nOne-off themes flagged for human review (not auto-renamed):")
        for t, n in sorted(one_offs, key=lambda kv: -kv[1]):
            print(f"  ?  `{t}`  ({n} rows)")

    if args.dry_run:
        return 0
    n = _apply_collapse(plan)
    print(f"\nApplied: {n} row(s) updated.")
    return 0


# ------------------------------- SPLIT PASS -------------------------------


SPLIT_SYSTEM = """You are normalizing one over-saturated coaching theme into sub-themes for a research-task knowledge base.

You'll receive: a theme name + its one-line definition + every row's
(Research Angle | Title | Relevance) joined.

Your job: propose 3-6 sub-themes that meaningfully partition this saturated
theme, then assign every input row to exactly one sub-theme. Sub-themes must:

- Be kebab-case slugs, distinct from the parent theme.
- Be substantively different (not synonyms of each other).
- Each one-line definition makes clear why a row belongs in that bucket and not
  in any other.
- Cover all rows — no orphans.

Output format (exact):

```
SUB-THEMES:
- <slug-1>: <one-line definition>
- <slug-2>: <one-line definition>
...

ASSIGNMENTS:
<row-id> -> <sub-theme-slug>
<row-id> -> <sub-theme-slug>
...
```

The <row-id> values are the integers I'll send you. Do not add other commentary.
"""


THEME_DEFINITIONS = {
    "deliberate-discomfort": (
        "The practice of seeking out friction intentionally — fasting, cold, "
        "fatigue, fear — as a deliberate training input rather than something "
        "to avoid."
    ),
    "comfort-as-default": (
        "The pattern of unconsciously defaulting to easier choices and the "
        "long-term debt this creates."
    ),
    "body-literacy": (
        "Reading interoceptive signals — pre-run fatigue, anticipatory dread, "
        "the body's forecasts — and parsing what they actually mean."
    ),
    "naming-the-fear": (
        "Articulating a specific feared outcome so it can be rehearsed rather "
        "than vaguely avoided."
    ),
    "preparedness-debt": (
        "The cumulative cost of comfortable choices — the gap between current "
        "capacity and what life will eventually demand."
    ),
    "strategic-vs-manufactured-suffering": (
        "Distinguishing suffering that builds capacity from suffering that "
        "feeds a self-narrative."
    ),
    "amcc-effect": (
        "The anterior mid-cingulate cortex as the neural substrate for "
        "choosing aversive effort — and how to train it."
    ),
}


def _run_split_for_theme(theme: str, theme_rows: List[Dict[str, Any]]) -> str:
    """Returns the model's raw split-proposal text for one saturated theme."""
    definition = THEME_DEFINITIONS.get(theme, "(no canonical definition)")
    listing = "\n".join(
        f"[{i}] angle={r['research_angle']!r} title={r['title'][:80]!r} relevance={r['relevance'][:120]!r}"
        for i, r in enumerate(theme_rows)
    )
    user_msg = (
        f"Theme: {theme}\n"
        f"Definition: {definition}\n\n"
        f"Rows ({len(theme_rows)}):\n{listing}"
    )
    result = call_llm(
        model="claude-sonnet-4-6",
        system_prompt=SPLIT_SYSTEM,
        user_message=user_msg,
        max_tokens=4000,
    )
    return result["text"]


def _proposal_path() -> Path:
    return PROPOSAL_DIR / f"sub-theme-proposal-{date.today().isoformat()}.md"


def cmd_split(args: argparse.Namespace) -> int:
    rows = collect_rows()
    by_theme: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for r in rows:
        if r["coaching_theme"] in CANONICAL_THEMES:
            by_theme[r["coaching_theme"]].append(r)
    saturated = {t: rs for t, rs in by_theme.items() if len(rs) >= SATURATION_THRESHOLD}

    if not saturated:
        print(f"No theme over {SATURATION_THRESHOLD} rows. Nothing to split.")
        return 0

    proposal_path = _proposal_path()
    PROPOSAL_DIR.mkdir(parents=True, exist_ok=True)

    if args.apply:
        if not proposal_path.exists():
            print(f"ERROR: --apply requires {proposal_path} (run with --dry-run first).")
            return 2
        return _apply_proposal(proposal_path, by_theme)

    # Dry-run: write proposal file
    sections: List[str] = [f"# Sub-theme proposal — {date.today().isoformat()}", ""]
    sections.append(
        "Generated by `pipeline/scripts/normalize_themes.py --split`. "
        "Edit sub-theme names/definitions or move row assignments below, then "
        "re-run with `--apply` to write changes to Notion. Original values are "
        "logged in `Theme Audit Log`."
    )
    sections.append("")
    for theme, theme_rows in saturated.items():
        print(f"Splitting {theme} ({len(theme_rows)} rows)...")
        body = _run_split_for_theme(theme, theme_rows)
        sections.append(f"## {theme}  (rows: {len(theme_rows)})")
        sections.append("")
        sections.append("```")
        sections.append(body)
        sections.append("```")
        sections.append("")
        sections.append("Row reference (index -> page_id):")
        sections.append("```")
        for i, r in enumerate(theme_rows):
            sections.append(f"{i}\t{r['page_id']}\t{r['title'][:80]}")
        sections.append("```")
        sections.append("")
    proposal_path.write_text("\n".join(sections))
    print(f"\nWrote proposal: {proposal_path}")
    print("Review (edit sub-theme names or row assignments), then re-run with --apply.")
    return 0


_SUBTHEME_RE = re.compile(r"^-\s+([a-z0-9-]+)\s*:\s*(.+)$")
_ASSIGN_RE = re.compile(r"^(\d+)\s*->\s*([a-z0-9-]+)$")


def _parse_proposal_section(section_text: str) -> tuple[Dict[str, str], Dict[int, str]]:
    sub_themes: Dict[str, str] = {}
    assignments: Dict[int, str] = {}
    in_sub = False
    in_assign = False
    for line in section_text.splitlines():
        line = line.strip()
        if line.startswith("SUB-THEMES"):
            in_sub, in_assign = True, False
            continue
        if line.startswith("ASSIGNMENTS"):
            in_sub, in_assign = False, True
            continue
        if in_sub:
            m = _SUBTHEME_RE.match(line)
            if m:
                sub_themes[m.group(1)] = m.group(2).strip()
        elif in_assign:
            m = _ASSIGN_RE.match(line)
            if m:
                assignments[int(m.group(1))] = m.group(2)
    return sub_themes, assignments


def _apply_proposal(
    proposal_path: Path,
    by_theme: Dict[str, List[Dict[str, Any]]],
) -> int:
    text = proposal_path.read_text()
    # Parse per-theme sections
    sections = re.split(r"^##\s+([a-z0-9-]+)\s+\(rows:.*?\)\s*$", text, flags=re.MULTILINE)
    # sections = [preamble, theme_1_name, theme_1_body, theme_2_name, ...]
    client = get_client()
    n = 0
    for i in range(1, len(sections), 2):
        theme = sections[i].strip()
        body = sections[i + 1]
        theme_rows = by_theme.get(theme, [])
        sub_themes, assignments = _parse_proposal_section(body)
        if not sub_themes or not assignments:
            print(f"  ! Could not parse proposal for `{theme}` — skipping.")
            continue
        print(f"  applying {theme}: {len(sub_themes)} sub-themes, {len(assignments)} assignments")
        for idx, sub_slug in assignments.items():
            if idx >= len(theme_rows):
                continue
            r = theme_rows[idx]
            audit_entry = f"{date.today().isoformat()}: split '{theme}' -> '{sub_slug}'"
            full_audit = (r["theme_audit_log"] + " | " + audit_entry) if r["theme_audit_log"] else audit_entry
            client.pages.update(
                page_id=r["page_id"],
                properties={
                    "Coaching Theme": {"rich_text": [{"text": {"content": sub_slug}}]},
                    "Theme Audit Log": {"rich_text": [{"text": {"content": full_audit[:1900]}}]},
                },
            )
            time.sleep(0.4)
            n += 1
    print(f"\nApplied: {n} row(s) updated.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--collapse", action="store_true", help="Variant collapse pass.")
    parser.add_argument("--split", action="store_true", help="Sub-theme split pass.")
    g = parser.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true")
    g.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)

    if not (args.collapse or args.split):
        print("Specify --collapse or --split.", file=sys.stderr)
        return 2

    if args.collapse:
        cmd_collapse(args)
    if args.split:
        cmd_split(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
