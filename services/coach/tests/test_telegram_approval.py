"""Approval flow: allowlist mutation + atomic write."""
from __future__ import annotations
import json
from pathlib import Path
from telegram_bot.allowlist import Allowlist


def test_add_user_persists_atomically(tmp_path: Path):
    p = tmp_path / "access.json"
    p.write_text(json.dumps({"version": 1, "policy": "allowlist", "allowed_user_ids": []}))
    a = Allowlist(p)
    assert a.allowed(42) is False

    added = a.add_user(42)
    assert added is True
    assert a.allowed(42) is True

    # Persisted to disk
    on_disk = json.loads(p.read_text())
    assert 42 in on_disk["allowed_user_ids"]
    assert on_disk["policy"] == "allowlist"


def test_add_user_idempotent(tmp_path: Path):
    p = tmp_path / "access.json"
    p.write_text(json.dumps({"version": 1, "policy": "allowlist", "allowed_user_ids": [42]}))
    a = Allowlist(p)
    added = a.add_user(42)
    assert added is False


def test_add_user_rejects_when_policy_not_allowlist(tmp_path: Path):
    p = tmp_path / "access.json"
    p.write_text(json.dumps({"version": 1, "policy": "open", "allowed_user_ids": []}))
    a = Allowlist(p)
    added = a.add_user(42)
    assert added is False
    assert a.allowed(42) is False
