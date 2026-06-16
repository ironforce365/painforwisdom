"""Guard: requirements.txt must mirror pyproject's deps exactly.

The Docker build keys its heavy (torch) install layer on requirements.txt, not
pyproject.toml, so a version-only release bump no longer busts it. That only
stays correct if requirements.txt lists exactly the same dependencies as
pyproject. If someone edits a dep in pyproject.toml and forgets to regenerate
requirements.txt, the image would silently build against stale deps — this test
fails loudly instead.
"""
from __future__ import annotations

import tomllib
from pathlib import Path

_COACH = Path(__file__).resolve().parent.parent


def _norm(req: str) -> str:
    # Normalise a requirement specifier for set comparison: drop surrounding
    # whitespace and inline comments, lowercase (PEP 503 names are
    # case-insensitive; specifiers here are already canonical).
    return req.split("#", 1)[0].strip().lower()


def _pyproject_deps() -> set[str]:
    data = tomllib.loads((_COACH / "pyproject.toml").read_text())
    proj = data["project"]
    deps = list(proj.get("dependencies", []))
    deps += list(proj.get("optional-dependencies", {}).get("test", []))
    return {_norm(d) for d in deps if _norm(d)}


def _requirements_txt() -> set[str]:
    lines = (_COACH / "requirements.txt").read_text().splitlines()
    return {_norm(line) for line in lines if _norm(line)}


def test_requirements_txt_matches_pyproject():
    pyproject = _pyproject_deps()
    requirements = _requirements_txt()
    missing = pyproject - requirements   # in pyproject, absent from manifest
    extra = requirements - pyproject     # in manifest, not a real dep
    assert not missing, f"requirements.txt is missing deps from pyproject: {sorted(missing)}"
    assert not extra, f"requirements.txt has deps not in pyproject (stale?): {sorted(extra)}"
