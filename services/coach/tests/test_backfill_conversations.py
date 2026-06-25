"""One-time backfill of the monitor conversation log from vault _inbox markdown."""
from __future__ import annotations

from pathlib import Path

from scripts.backfill_conversations import backfill, parse_entry
from telegram_bot.conversation_log import ConversationLog

_ENTRY = """---
user_id: {uid}
timestamp: {ts}
retrieved_sources: ['application', 'theory']
---

## User

{user}

## Coach

{coach}
"""


def _write(dir_: Path, ts: str, user: str, coach: str) -> None:
    dir_.mkdir(parents=True, exist_ok=True)
    (dir_ / f"{ts}.md").write_text(
        _ENTRY.format(uid=dir_.name, ts=ts, user=user, coach=coach), encoding="utf-8"
    )


def test_parse_entry_extracts_ts_and_both_sections():
    ts, user, coach = parse_entry(
        _ENTRY.format(uid="9", ts="20260622T110717Z", user="hola coach", coach="¿Qué querés?")
    )
    assert ts == "2026-06-22T11:07:17+00:00"
    assert user == "hola coach"
    assert coach == "¿Qué querés?"


def test_backfill_replays_in_order_and_skips_synthetic(tmp_path: Path):
    inbox = tmp_path / "_inbox"
    _write(inbox / "42", "20260101T090000Z", "first msg", "first reply")
    _write(inbox / "42", "20260102T090000Z", "second msg", "second reply")
    _write(inbox / "smoke-test-v014", "20260101T000000Z", "synthetic", "synthetic")

    log = ConversationLog(tmp_path / "conv")
    summary = backfill(inbox, log)

    assert summary == {"42": 2}  # synthetic dir skipped
    msgs = log.read_conversation("42")
    assert [m["role"] for m in msgs] == ["user", "coach", "user", "coach"]
    assert msgs[0]["text"] == "first msg"
    assert msgs[-1]["text"] == "second reply"
    assert msgs[0]["ts"] == "2026-01-01T09:00:00+00:00"
    assert log.read_conversation("smoke-test-v014") == []


def test_backfill_is_idempotent(tmp_path: Path):
    inbox = tmp_path / "_inbox"
    _write(inbox / "7", "20260101T090000Z", "hi", "hello")
    log = ConversationLog(tmp_path / "conv")

    backfill(inbox, log)
    backfill(inbox, log)  # second run must not duplicate

    assert len(log.read_conversation("7")) == 2  # one user + one coach, not four
