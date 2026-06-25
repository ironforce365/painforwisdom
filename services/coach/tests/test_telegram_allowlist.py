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


def test_add_user_persists_in_place_without_replacing_the_file(tmp_path: Path):
    # access.json is bind-mounted as a SINGLE FILE in prod; an atomic tmp+rename
    # write does not propagate through a single-file bind mount (the rename can't
    # cross the mountpoint), so runtime approvals silently vanished on restart.
    # add_user must overwrite the file IN PLACE — same inode, no .tmp left behind.
    p = tmp_path / "access.json"
    p.write_text(json.dumps({"version": 1, "policy": "allowlist", "allowed_user_ids": [42]}))
    inode_before = p.stat().st_ino

    a = Allowlist(p)
    assert a.add_user(99) is True
    assert a.allowed(99) is True

    # Wrote through to the SAME inode (no rename), with no stray temp file.
    assert p.stat().st_ino == inode_before
    assert not (tmp_path / "access.json.tmp").exists()

    # A fresh reader (simulating a restart re-reading the host file) sees it.
    assert Allowlist(p).allowed(99) is True


def test_add_user_idempotent(tmp_path: Path):
    p = tmp_path / "access.json"
    p.write_text(json.dumps({"version": 1, "policy": "allowlist", "allowed_user_ids": [42]}))
    a = Allowlist(p)
    assert a.add_user(42) is False  # already present
