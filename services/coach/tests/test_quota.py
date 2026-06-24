"""Per-user daily message quota: 100 messages/day, counter resets at midnight.

The caller passes the current local date in, so reset behaviour is deterministic
and timezone handling lives in one tested helper (local_today)."""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

from telegram_bot.quota import DailyQuota, local_today

D1 = date(2026, 6, 21)
D2 = date(2026, 6, 22)


def test_first_message_is_allowed_and_counts_one(tmp_path: Path):
    q = DailyQuota(tmp_path / "quota.json", limit=3)
    res = q.check_and_increment(42, D1)
    assert res.allowed is True
    assert res.count == 1
    assert res.limit == 3


def test_counts_up_to_limit_then_blocks(tmp_path: Path):
    q = DailyQuota(tmp_path / "quota.json", limit=3)
    assert q.check_and_increment(42, D1).count == 1
    assert q.check_and_increment(42, D1).count == 2
    third = q.check_and_increment(42, D1)
    assert third.allowed is True and third.count == 3
    # 4th message of the day is blocked; the counter does not climb past the limit.
    fourth = q.check_and_increment(42, D1)
    assert fourth.allowed is False
    assert fourth.count == 3


def test_counter_resets_on_new_day(tmp_path: Path):
    q = DailyQuota(tmp_path / "quota.json", limit=2)
    q.check_and_increment(42, D1)
    blocked = q.check_and_increment(42, D1)  # count 2, still allowed
    assert blocked.allowed is True
    assert q.check_and_increment(42, D1).allowed is False  # 3rd blocked
    # New day → fresh allowance.
    nxt = q.check_and_increment(42, D2)
    assert nxt.allowed is True
    assert nxt.count == 1


def test_users_are_independent(tmp_path: Path):
    q = DailyQuota(tmp_path / "quota.json", limit=1)
    assert q.check_and_increment(1, D1).allowed is True
    assert q.check_and_increment(1, D1).allowed is False
    # A different user has their own fresh allowance.
    assert q.check_and_increment(2, D1).allowed is True


def test_persists_across_instances(tmp_path: Path):
    path = tmp_path / "quota.json"
    DailyQuota(path, limit=2).check_and_increment(9, D1)
    DailyQuota(path, limit=2).check_and_increment(9, D1)
    # A fresh instance (restart) sees the prior count → 3rd is blocked.
    assert DailyQuota(path, limit=2).check_and_increment(9, D1).allowed is False


def test_persisted_file_shape(tmp_path: Path):
    path = tmp_path / "quota.json"
    DailyQuota(path, limit=5).check_and_increment(9, D1)
    data = json.loads(path.read_text())
    assert data["date"] == "2026-06-21"
    assert data["counts"]["9"] == 1


def test_local_today_shifts_with_timezone():
    # 03:00 UTC on the 21st is still the 20th in Los Angeles (UTC-7 in June).
    now = datetime(2026, 6, 21, 3, 0, tzinfo=timezone.utc)
    assert local_today(now, "America/Los_Angeles") == date(2026, 6, 20)
    assert local_today(now, "UTC") == date(2026, 6, 21)


def test_local_today_unknown_tz_falls_back_to_utc():
    now = datetime(2026, 6, 21, 3, 0, tzinfo=timezone.utc)
    assert local_today(now, "Not/AZone") == date(2026, 6, 21)
