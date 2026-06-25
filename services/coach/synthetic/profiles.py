"""Synthetic-user profiles.

A profile is a YAML persona that a driver agent impersonates to hold a
conversation with the coach. Used to test the coach end to end, at scale (many
concurrent personas), over long conversations (>100 turns), and — once it lands
— its personalization behaviour. Profiles are NOT real users: their turns run on
the test channel, so they never feed the vault inbox / knowledge base.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

_REQUIRED = ("slug", "name", "persona", "opener", "turn_count")


@dataclass(frozen=True)
class Profile:
    slug: str
    name: str
    persona: str
    opener: str
    turn_count: int
    style: str = ""
    goals: str = ""

    def user_id(self) -> str:
        """Stable, filesystem-safe, namespaced id. The ``synthetic-`` prefix keeps
        it from ever colliding with a numeric Telegram id and lets the bot's
        outreach scan skip it (non-numeric)."""
        return f"synthetic-{self.slug}"


def load_profile(path: Path, *, turn_count: int | None = None) -> Profile:
    """Load one profile YAML. ``turn_count`` overrides the file value (e.g. to
    crank a short profile up to a 100+-turn long-conversation test)."""
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    missing = [k for k in _REQUIRED if k not in data]
    if missing:
        raise ValueError(f"{path}: profile missing required field(s): {missing}")
    return Profile(
        slug=str(data["slug"]),
        name=str(data["name"]),
        persona=str(data["persona"]),
        opener=str(data["opener"]),
        turn_count=int(turn_count if turn_count is not None else data["turn_count"]),
        style=str(data.get("style", "")),
        goals=str(data.get("goals", "")),
    )


def load_profiles_dir(directory: Path, *, turn_count: int | None = None) -> list[Profile]:
    """Load every ``*.yaml`` profile in a directory, sorted by filename."""
    return [
        load_profile(p, turn_count=turn_count)
        for p in sorted(Path(directory).glob("*.yaml"))
    ]
