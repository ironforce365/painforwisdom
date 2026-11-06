"""C10 publishing config (spec 2026-11-03-console-operability §1.2). Standard library only: the UI server may import it."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

CONFIG_PATH = Path(os.environ.get("PIPELINE_PUBLISHING_CONFIG")  # tests only
                   or Path(__file__).resolve().parent.parent / "config" / "publishing.json")


def _deep_merge(base: dict[str, Any], over: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in over.items():
        out[key] = _deep_merge(out[key], value) if isinstance(value, dict) and isinstance(out.get(key), dict) else value
    return out


def load(profile: str) -> dict[str, Any]:
    raw = json.loads(CONFIG_PATH.read_text())
    profiles = raw.pop("profiles", {})
    return _deep_merge(raw, profiles.get(profile, {}))


def current_profile() -> str:
    """The profile pipeline.run was started with; prod is run.py's own default."""
    return os.environ.get("PIPELINE_PROFILE") or "prod"


def run_language(chosen: str | None = None) -> str:
    """The language chosen for the run. Empty for a run from before the choice existed: the catalog's."""
    return chosen or load(current_profile())["language"]
