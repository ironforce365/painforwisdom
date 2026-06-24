"""Persistent 'has this user been welcomed' registry. Mirrors the Allowlist
atomic-write pattern so the welcome fires exactly once per user and survives
bot restarts (otherwise every redeploy would re-welcome everyone)."""
from __future__ import annotations

import json
from pathlib import Path

from telegram_bot.welcome import WelcomeRegistry


def test_unseen_user_is_not_marked(tmp_path: Path):
    reg = WelcomeRegistry(tmp_path / "welcomed.json")
    assert reg.seen(42) is False


def test_mark_then_seen(tmp_path: Path):
    reg = WelcomeRegistry(tmp_path / "welcomed.json")
    newly = reg.mark(42)
    assert newly is True
    assert reg.seen(42) is True


def test_mark_is_idempotent(tmp_path: Path):
    reg = WelcomeRegistry(tmp_path / "welcomed.json")
    assert reg.mark(42) is True
    assert reg.mark(42) is False  # already welcomed


def test_persists_across_instances(tmp_path: Path):
    path = tmp_path / "welcomed.json"
    WelcomeRegistry(path).mark(7)
    # A fresh instance (simulating a restart) still sees the user.
    assert WelcomeRegistry(path).seen(7) is True


def test_missing_file_is_tolerated_and_created(tmp_path: Path):
    path = tmp_path / "nested" / "welcomed.json"
    reg = WelcomeRegistry(path)  # parent dir does not exist yet
    assert reg.seen(1) is False
    reg.mark(1)
    assert path.exists()
    data = json.loads(path.read_text())
    assert 1 in data["welcomed_user_ids"]
