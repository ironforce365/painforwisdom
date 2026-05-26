"""Allowlist enforces ID list and policy."""
from __future__ import annotations
import json
from pathlib import Path
from telegram_bot.allowlist import Allowlist


def test_allows_listed_user(tmp_path: Path):
    p = tmp_path / "access.json"
    p.write_text(json.dumps({"version": 1, "policy": "allowlist", "allowed_user_ids": [42, 99]}))
    a = Allowlist(p)
    assert a.allowed(42) is True
    assert a.allowed(99) is True
    assert a.allowed(123) is False


def test_empty_allowlist_blocks_everyone(tmp_path: Path):
    p = tmp_path / "access.json"
    p.write_text(json.dumps({"version": 1, "policy": "allowlist", "allowed_user_ids": []}))
    a = Allowlist(p)
    assert a.allowed(42) is False


def test_unknown_policy_rejects(tmp_path: Path):
    p = tmp_path / "access.json"
    p.write_text(json.dumps({"version": 1, "policy": "open", "allowed_user_ids": [42]}))
    a = Allowlist(p)
    assert a.allowed(42) is False
