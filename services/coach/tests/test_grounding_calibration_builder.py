from eval.grounding.build_calibration_set import extract_source, sample_claims


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
