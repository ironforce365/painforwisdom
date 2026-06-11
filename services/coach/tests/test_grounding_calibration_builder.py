import pytest

from eval.grounding.build_calibration_set import (
    extract_source,
    parse_checked_slugs,
    pick_one_per_entry,
    resolve_slugs,
    sample_claims,
)


def test_extract_source_pulls_factual_sections():
    entry = (
        "# Title\n\n## Core Insight\nAn already-interpreted insight.\n\n"
        "## Story Anchor\nGonzalo chose the steepest trail in a storm.\n\n"
        "## Framework Connection\nTheory stuff.\n\n"
        "## Raw Transcript Notes\n- \"it was perfect\"\n"
    )
    src = extract_source(entry)
    assert "steepest trail" in src
    assert "it was perfect" in src
    assert "already-interpreted insight" not in src  # Core Insight excluded
    assert "Theory stuff" not in src                  # Framework excluded


def test_extract_source_empty_when_no_sections():
    assert extract_source("# Title\n\n## Other\nnothing relevant") == ""


def test_sample_claims_deterministic_and_weights_interpretations():
    rows = [{"type": "interpretation", "claim": f"i{n}", "entry": "e", "source": "s", "conf": 6} for n in range(10)]
    rows += [{"type": "fact", "claim": f"f{n}", "entry": "e", "source": "s", "conf": None} for n in range(10)]
    a = sample_claims(rows, sample=10, seed=7)
    b = sample_claims(rows, sample=10, seed=7)
    assert [r["claim"] for r in a] == [r["claim"] for r in b]  # deterministic with seed
    n_interp = sum(1 for r in a if r["type"] == "interpretation")
    assert n_interp >= 8  # interpretation-weighted (~80%)
    assert len(a) == 10


def test_parse_checked_slugs_takes_only_checked_bold_slugs():
    menu = (
        "## RECOMMENDED\n"
        "- [x] **storm-as-perfect-test** — chose the steepest trail\n"
        "- [x] **rest-without-guilt** — week off, zero guilt\n"
        "## SWAP-IN POOL\n"
        "- [ ] **preparedness-debt** — hit the wall\n"
        "- [ ] **micro-overrides-as-training** — phone down\n"
    )
    assert parse_checked_slugs(menu) == ["storm-as-perfect-test", "rest-without-guilt"]


def test_parse_checked_slugs_ignores_table_rows_and_uppercase_x():
    menu = (
        "- [X] **upper-x-counts**\n"            # capital X is still checked
        "- [x] **lower-x-counts**\n"
        "| storm-as-perfect-test | 2026 | STRONG |\n"  # catalog table row, not a checkbox
    )
    assert parse_checked_slugs(menu) == ["upper-x-counts", "lower-x-counts"]


def test_resolve_slugs_maps_each_slug_to_exactly_one_path(tmp_path):
    (tmp_path / "2026-02-17-storm-as-perfect-test.md").write_text("x")
    (tmp_path / "2026-04-23-limits-as-intelligence.md").write_text("x")
    (tmp_path / "2026-02-25-limits-as-hypotheses.md").write_text("x")
    paths = resolve_slugs(tmp_path, ["limits-as-hypotheses", "storm-as-perfect-test"])
    # order preserved; suffix match is exact (does not confuse the two limits-* entries)
    assert [p.stem for p in paths] == [
        "2026-02-25-limits-as-hypotheses",
        "2026-02-17-storm-as-perfect-test",
    ]


def test_resolve_slugs_raises_on_missing_slug(tmp_path):
    (tmp_path / "2026-02-17-storm-as-perfect-test.md").write_text("x")
    with pytest.raises(ValueError, match="no-such-entry"):
        resolve_slugs(tmp_path, ["no-such-entry"])


def test_pick_one_per_entry_one_claim_each_interpretation_preferred():
    rows = [
        {"entry": "e1", "type": "fact", "claim": "e1-fact", "conf": None},
        {"entry": "e1", "type": "interpretation", "claim": "e1-interp", "conf": 7},
        {"entry": "e2", "type": "interpretation", "claim": "e2-interp", "conf": 6},
        {"entry": "e3", "type": "fact", "claim": "e3-fact", "conf": None},  # no interp -> fallback
    ]
    picked = pick_one_per_entry(rows, seed=7)
    by_entry = {r["entry"]: r for r in picked}
    assert len(picked) == 3                       # one per distinct entry
    assert by_entry["e1"]["type"] == "interpretation"  # interpretation preferred over fact
    assert by_entry["e3"]["type"] == "fact"            # falls back when no interpretation
    # deterministic with seed
    assert [r["claim"] for r in pick_one_per_entry(rows, seed=7)] == [r["claim"] for r in picked]
