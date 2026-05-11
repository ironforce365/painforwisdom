"""Render the research-curator agent prompt's theme taxonomy from themes.db.

The previous version of `.claude/agents/research-curator.md` baked the
sub-theme listing + priority order into the prompt text. Adding a theme
required editing the prompt and the runtime dict in lockstep.

Now the prompt has marked auto-region between
    <!-- AUTO-THEMES-START -->
    <!-- AUTO-THEMES-END -->
and this script writes the up-to-date taxonomy between them. The agent
prompt is *checked into the repo* (Claude Code loads it from disk at
invocation time, so live DB lookup isn't an option) — running this script
after seeding/changing themes regenerates the in-prompt block.

Usage:
    python -m pipeline.scripts.render_curator_taxonomy          # dry-run, print to stdout
    python -m pipeline.scripts.render_curator_taxonomy --apply  # rewrite the prompt file
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from pipeline.themes_db import Theme, connect, list_active, list_dead, list_children


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
PROMPT_PATH = PROJECT_ROOT / ".claude" / "agents" / "research-curator.md"

MARKER_START = "<!-- AUTO-THEMES-START -->"
MARKER_END = "<!-- AUTO-THEMES-END -->"


def _render_subtheme_block(umbrella: Theme, children: list[Theme]) -> str:
    lines = [
        f"Umbrella `{umbrella.name}` is DEAD — pick one of:",
    ]
    for c in children:
        lines.append(f"- `{c.name}` — {c.definition}")
    return "\n".join(lines)


def _render_terminal_block(top_level_terminals: list[Theme]) -> str:
    lines = ["Other terminal themes (still pick as-is, no sub-split):"]
    for t in top_level_terminals:
        lines.append(f"- `{t.name}` — {t.definition}")
    lines.append(
        "- Plus organically-grown themes already present in "
        "`theme_stats.json` — read it before picking, the "
        "saturated/non-saturated annotation is authoritative."
    )
    return "\n".join(lines)


def _render_priority_block(active: list[Theme]) -> str:
    ranked = sorted(
        [t for t in active if t.priority < 900],
        key=lambda t: (t.priority, t.name),
    )
    lines = ["**Priority order when a reference could belong to multiple themes:**"]
    for i, t in enumerate(ranked, start=1):
        lines.append(f"{i}. {t.priority_rule} → `{t.name}`")
    lines.append("")
    lines.append(
        "When in doubt: pick the theme whose agent prompt would produce the "
        "most specific, grounded deep dive for this reference. A precise "
        "match beats a broad one. Never pick a dead umbrella directly — "
        "those route through their sub-themes only."
    )
    return "\n".join(lines)


def render() -> str:
    conn = connect()
    try:
        active = list_active(conn)
        dead_umbrellas = list_dead(conn)
        out: list[str] = [
            MARKER_START,
            "<!-- DO NOT EDIT — regenerated from pipeline/state/themes.db -->",
            "<!-- Source: python -m pipeline.scripts.render_curator_taxonomy --apply -->",
            "",
            "**Sub-theme taxonomy (sourced from `themes.db`).** Some umbrella "
            "themes were split into sub-themes. **You MUST pick the sub-theme, "
            "never the dead umbrella.**",
            "",
        ]
        for umbrella in dead_umbrellas:
            children = list_children(conn, umbrella.name)
            out.append(_render_subtheme_block(umbrella, children))
            out.append("")
        top_level_terminals = [t for t in active if t.parent is None]
        out.append(_render_terminal_block(top_level_terminals))
        out.append("")
        out.append(_render_priority_block(active))
        out.append("")
        out.append(MARKER_END)
        return "\n".join(out)
    finally:
        conn.close()


def _replace_between_markers(text: str, new_block: str) -> str:
    start_idx = text.find(MARKER_START)
    end_idx = text.find(MARKER_END)
    if start_idx == -1 or end_idx == -1:
        raise RuntimeError(
            f"Markers not found in {PROMPT_PATH}. Add\n"
            f"  {MARKER_START}\n  ...(content)\n  {MARKER_END}\n"
            "around the auto-rendered taxonomy block."
        )
    if end_idx < start_idx:
        raise RuntimeError(f"END marker appears before START marker in {PROMPT_PATH}.")
    end_full = end_idx + len(MARKER_END)
    return text[:start_idx] + new_block + text[end_full:]


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write back to the prompt file (default: dry-run to stdout)",
    )
    parser.add_argument(
        "--path",
        type=Path,
        default=PROMPT_PATH,
        help=f"Prompt file to rewrite (default: {PROMPT_PATH})",
    )
    args = parser.parse_args(argv)

    block = render()

    if not args.apply:
        print(block)
        return 0

    if not args.path.exists():
        print(f"error: prompt file not found: {args.path}", file=sys.stderr)
        return 2

    original = args.path.read_text()
    updated = _replace_between_markers(original, block)
    if original == updated:
        print(f"✓ {args.path.name} already up to date.")
        return 0
    args.path.write_text(updated)
    print(f"✓ {args.path.name} taxonomy rendered from themes.db ({len(block)} chars).")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
