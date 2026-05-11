"""Canonical theme taxonomy — single source of truth (in-code).

Two consumers:
1. `pipeline.themes_db.connect()` auto-seeds this list into the DB on first
   connect, so a fresh checkout works without a manual setup step (note:
   `pipeline/state/` is gitignored, so the .db file is regenerated per host).
2. `python -m pipeline.scripts.seed_themes_db` re-applies the list explicitly
   after the operator edits this file.

The seed data replaces the static taxonomy that previously lived in
- pipeline/nodes/notion_research.py (`_THEME_TO_AGENT`)
- .claude/agents/research-curator.md (sub-theme listing + priority order)

After editing this file, run:
    python -m pipeline.scripts.seed_themes_db          # idempotent re-apply
    python -m pipeline.scripts.render_curator_taxonomy --apply  # re-render prompt
"""
from __future__ import annotations

from pipeline.themes_db import Theme


SEED: list[Theme] = [
    # ── Top-level terminal themes (active, no sub-split) ────────────────────
    Theme(
        name="body-literacy",
        parent=None,
        status="active",
        agent="body-literacy",
        definition="interoception, fatigue signals, physiological self-reading.",
        priority=1,
        priority_rule="Reference addresses a specific physiological or interoceptive mechanism.",
    ),
    Theme(
        name="amcc-effect",
        parent=None,
        status="active",
        agent="amcc-effect",
        definition=(
            "specifically the anterior mid-cingulate cortex literature "
            "(effortful-choice neural override). Prefer over "
            "`neuroscience-of-voluntary-effort` when the reference is "
            "directly about aMCC."
        ),
        priority=2,
        priority_rule="Reference is directly about aMCC literature.",
    ),
    Theme(
        name="strategic-vs-manufactured-suffering",
        parent=None,
        status="active",
        agent="strategic-vs-manufactured-suffering",
        definition=(
            "meaning, cost accounting, ethics of chosen suffering "
            "(Frankl, voluntary-suffering decisions)."
        ),
        priority=4,
        priority_rule="Reference is about meaning, cost, or ethics of chosen suffering.",
    ),
    Theme(
        name="naming-the-fear",
        parent=None,
        status="active",
        agent="naming-the-fear",
        definition="articulating specific fears, fear-setting protocols.",
        priority=5,
        priority_rule="Reference is about articulating fear.",
    ),
    Theme(
        name="preparedness-debt",
        parent=None,
        status="active",
        agent="preparedness-debt",
        definition="cumulative cost of comfort choices over time.",
        priority=6,
        priority_rule="Reference is about cumulative comfort-debt.",
    ),

    # ── Dead umbrella: comfort-as-default ────────────────────────────────────
    Theme(
        name="comfort-as-default",
        parent=None,
        status="dead",
        agent="comfort-as-default",
        definition=(
            "DEAD umbrella (split 2026-05-11). Never pick directly — "
            "use a sub-theme."
        ),
        priority=999,
        priority_rule="(dead umbrella — never picked directly)",
    ),
    Theme(
        name="neurological-basis-of-override",
        parent="comfort-as-default",
        status="active",
        agent="comfort-as-default",
        definition=(
            "brain structures (aMCC, dopamine) explaining why effortful "
            "choice strengthens or weakens resistance to comfort defaults."
        ),
        priority=20,
        priority_rule=(
            "Reference is about brain-level resistance to comfort defaults "
            "(not aMCC-specific — use `amcc-effect` then)."
        ),
    ),
    Theme(
        name="comfort-creep-and-self-deception",
        parent="comfort-as-default",
        status="active",
        agent="comfort-as-default",
        definition=(
            "narrative maintenance, cognitive ease, friction-blindness "
            "keeping people locked in comfort patterns unnoticed."
        ),
        priority=21,
        priority_rule=(
            "Reference is about avoidance patterns specifically — narrative/"
            "self-deception flavor."
        ),
    ),
    Theme(
        name="procrastination-and-avoidance-mechanics",
        parent="comfort-as-default",
        status="active",
        agent="comfort-as-default",
        definition=(
            "mood-regulation and intention-behavior dynamics making "
            "postponement the default response to uncomfortable tasks."
        ),
        priority=22,
        priority_rule=(
            "Reference is about avoidance patterns specifically — "
            "procrastination/postponement flavor."
        ),
    ),
    Theme(
        name="deliberate-discomfort-as-practice",
        parent="comfort-as-default",
        status="active",
        agent="comfort-as-default",
        definition=(
            "frameworks for voluntarily reintroducing hardship as "
            "structured countermeasure to comfort defaults."
        ),
        priority=23,
        priority_rule=(
            "Reference is a *framework* for reintroducing hardship as a "
            "countermeasure to comfort defaults."
        ),
    ),
    Theme(
        name="motivation-gap-and-habit-formation",
        parent="comfort-as-default",
        status="active",
        agent="comfort-as-default",
        definition=(
            "gap between inspiration and action; implementation intentions, "
            "habit accumulation, internalized motivation."
        ),
        priority=24,
        priority_rule="Reference is about habit / intention-action gap.",
    ),
    Theme(
        name="failure-response-and-recovery",
        parent="comfort-as-default",
        status="active",
        agent="comfort-as-default",
        definition=(
            "post-lapse dynamics (shame, self-compassion, abstinence "
            "violation effects) that sustain or entrench comfort defaults."
        ),
        priority=25,
        priority_rule="Reference is about post-failure recovery dynamics.",
    ),

    # ── Dead umbrella: deliberate-discomfort ────────────────────────────────
    Theme(
        name="deliberate-discomfort",
        parent=None,
        status="dead",
        agent="deliberate-discomfort",
        definition=(
            "DEAD umbrella (split 2026-05-11). Never pick directly — "
            "use a sub-theme."
        ),
        priority=999,
        priority_rule="(dead umbrella — never picked directly)",
    ),
    Theme(
        name="neuroscience-of-voluntary-effort",
        parent="deliberate-discomfort",
        status="active",
        agent="deliberate-discomfort",
        definition=(
            "brain mechanisms (aMCC, central governor, willpower circuitry) "
            "explaining why chosen discomfort builds tenacity."
        ),
        priority=3,
        priority_rule=(
            "Reference is about voluntary-effort neuroscience that is NOT "
            "aMCC-specific."
        ),
    ),
    Theme(
        name="hormesis-and-stress-adaptation",
        parent="deliberate-discomfort",
        status="active",
        agent="deliberate-discomfort",
        definition=(
            "calibrated doses of stressors producing overcompensation and "
            "resilience (physical, thermal, cognitive)."
        ),
        priority=14,
        priority_rule=(
            "Reference is about hormesis/stress-adaptation as biological "
            "principle."
        ),
    ),
    Theme(
        name="heat-and-physical-hardship-protocols",
        parent="deliberate-discomfort",
        status="active",
        agent="deliberate-discomfort",
        definition=(
            "specific physical stressors (heat acclimation, rucking, "
            "fatigue, sleep deprivation) as trainable inputs."
        ),
        priority=7,
        priority_rule=(
            "Reference describes specific deliberate physical protocols "
            "(heat, rucking, sleep dep)."
        ),
    ),
    Theme(
        name="stoic-and-philosophical-practice",
        parent="deliberate-discomfort",
        status="active",
        agent="deliberate-discomfort",
        definition=(
            "philosophical traditions (Stoic, Goggins-ian, virtue-ethics) "
            "framing voluntary discomfort as discipline."
        ),
        priority=8,
        priority_rule=(
            "Reference is grounded in philosophy/Stoicism/identity."
        ),
    ),
    Theme(
        name="failure-and-friction-as-diagnostic-tool",
        parent="deliberate-discomfort",
        status="active",
        agent="deliberate-discomfort",
        definition=(
            "treating adverse outcomes as data to decode and act on "
            "rather than avoid."
        ),
        priority=9,
        priority_rule="Reference treats failure as diagnostic data.",
    ),
    Theme(
        name="cognitive-reappraisal-and-reframing",
        parent="deliberate-discomfort",
        status="active",
        agent="deliberate-discomfort",
        definition=(
            "mental reinterpretation of discomfort, obstacles, or stress "
            "so friction becomes signal rather than threat."
        ),
        priority=10,
        priority_rule="Reference is about cognitive reappraisal/reframing.",
    ),
]
