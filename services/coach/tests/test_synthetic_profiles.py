"""Synthetic-user profiles: a YAML persona an agent impersonates to chat with
the coach (E2E / scalability / long-conversation / personalization testing)."""
from __future__ import annotations

from pathlib import Path

import pytest

from synthetic.profiles import Profile, load_profile, load_profiles_dir

SAMPLE = """\
slug: anxious-marathoner
name: Dana (test)
persona: |
  34, first-time marathoner, 8 weeks out, anxious she isn't training enough.
style: Short messages, lots of questions, second-guesses herself.
opener: Hey coach, I'm not sure I'm ready for my marathon.
turn_count: 6
goals: Finish the marathon without walking.
"""


def test_load_profile_parses_all_fields(tmp_path: Path):
    p = tmp_path / "dana.yaml"
    p.write_text(SAMPLE)
    prof = load_profile(p)
    assert isinstance(prof, Profile)
    assert prof.slug == "anxious-marathoner"
    assert prof.name == "Dana (test)"
    assert "first-time marathoner" in prof.persona
    assert prof.turn_count == 6
    assert prof.opener.startswith("Hey coach")
    assert prof.goals  # optional but present here


def test_turn_count_can_be_overridden(tmp_path: Path):
    # The runner can crank turns up (e.g. >100) without editing the file.
    p = tmp_path / "dana.yaml"
    p.write_text(SAMPLE)
    prof = load_profile(p, turn_count=150)
    assert prof.turn_count == 150


def test_missing_required_field_is_rejected(tmp_path: Path):
    p = tmp_path / "bad.yaml"
    p.write_text("slug: x\nname: y\n")  # no persona/opener/turn_count
    with pytest.raises(ValueError):
        load_profile(p)


def test_load_profiles_dir_loads_all_yaml_sorted(tmp_path: Path):
    (tmp_path / "b.yaml").write_text(SAMPLE.replace("anxious-marathoner", "b-slug"))
    (tmp_path / "a.yaml").write_text(SAMPLE.replace("anxious-marathoner", "a-slug"))
    (tmp_path / "notes.txt").write_text("ignored")
    profs = load_profiles_dir(tmp_path)
    assert [p.slug for p in profs] == ["a-slug", "b-slug"]


def test_shipped_example_profiles_are_valid():
    # The bundled personas must always parse (guard against broken YAML).
    shipped = Path(__file__).resolve().parents[1] / "synthetic" / "profiles"
    profs = load_profiles_dir(shipped)
    assert len(profs) >= 4
    assert all(p.opener and p.persona and p.turn_count > 0 for p in profs)
    # Slugs are unique (they become user ids / log filenames).
    slugs = [p.slug for p in profs]
    assert len(slugs) == len(set(slugs))


def test_synthetic_user_id_is_filesystem_safe_and_namespaced():
    prof = Profile(slug="anxious-marathoner", name="Dana", persona="x",
                   style="y", opener="hi", turn_count=3)
    uid = prof.user_id()
    assert uid == "synthetic-anxious-marathoner"
    assert "/" not in uid and ":" not in uid  # safe as a log filename
