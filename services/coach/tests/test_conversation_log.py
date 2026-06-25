"""Append-only per-user conversation log (JSONL), written by the bot and read by
the monitoring UI. One file per user: <root>/<user_id>.jsonl, one JSON record
per line {ts, role, text, name}. Reads are capped (default 5MB) so a long
history can't blow up the browser."""
from __future__ import annotations

from pathlib import Path

from telegram_bot.conversation_log import ConversationLog


def test_append_then_read_roundtrips(tmp_path: Path):
    log = ConversationLog(tmp_path)
    log.append(99, "user", "hola coach", name="Ana", ts="2026-06-21T10:00:00+00:00")
    log.append(99, "coach", "Hola Ana, ¿en qué trabajas?", ts="2026-06-21T10:00:30+00:00")

    msgs = log.read_conversation(99)
    assert [m["role"] for m in msgs] == ["user", "coach"]
    assert msgs[0]["text"] == "hola coach"
    assert msgs[1]["text"] == "Hola Ana, ¿en qué trabajas?"


def test_list_users_reports_name_last_time_and_last_text(tmp_path: Path):
    log = ConversationLog(tmp_path)
    log.append(1, "user", "first", name="Ana", ts="2026-06-21T10:00:00+00:00")
    log.append(1, "coach", "reply to ana", ts="2026-06-21T10:01:00+00:00")

    users = log.list_users()
    assert len(users) == 1
    u = users[0]
    assert u["user_id"] == "1"
    assert u["name"] == "Ana"           # latest known name (coach record had none)
    assert u["last_ts"] == "2026-06-21T10:01:00+00:00"
    assert u["last_text"] == "reply to ana"
    assert u["last_role"] == "coach"


def test_list_users_sorted_most_recent_first(tmp_path: Path):
    log = ConversationLog(tmp_path)
    log.append(1, "user", "older", name="Ana", ts="2026-06-21T09:00:00+00:00")
    log.append(2, "user", "newer", name="Bob", ts="2026-06-21T12:00:00+00:00")

    users = log.list_users()
    assert [u["user_id"] for u in users] == ["2", "1"]


def test_read_conversation_missing_user_is_empty(tmp_path: Path):
    assert ConversationLog(tmp_path).read_conversation(404) == []


def test_read_conversation_capped_at_max_bytes(tmp_path: Path):
    log = ConversationLog(tmp_path)
    # ~20KB of records, read back with a 5KB cap → only the tail survives.
    for i in range(200):
        log.append(7, "user", f"message number {i} " + "x" * 80,
                   ts=f"2026-06-21T10:{i // 60:02d}:{i % 60:02d}+00:00")

    msgs = log.read_conversation(7, max_bytes=5000)
    # Capped: we dropped the earliest messages...
    assert msgs[0]["text"] != "message number 0 " + "x" * 80
    # ...kept the most recent...
    assert "message number 199" in msgs[-1]["text"]
    # ...and every returned line parsed cleanly (no half-truncated record).
    assert all("text" in m and "role" in m for m in msgs)


def test_append_creates_root_dir(tmp_path: Path):
    root = tmp_path / "does" / "not" / "exist"
    log = ConversationLog(root)
    log.append(5, "user", "hi", ts="2026-06-21T10:00:00+00:00")
    assert (root / "5.jsonl").exists()


def test_clear_removes_only_that_users_history(tmp_path: Path):
    log = ConversationLog(tmp_path)
    log.append(1, "user", "keep me", ts="2026-06-21T10:00:00+00:00")
    log.append(2, "user", "delete me", ts="2026-06-21T10:00:00+00:00")

    log.clear(2)

    assert log.read_conversation(2) == []                     # gone
    assert log.read_conversation(1)[0]["text"] == "keep me"   # untouched
    assert [u["user_id"] for u in log.list_users()] == ["1"]


def test_clear_missing_user_is_noop(tmp_path: Path):
    log = ConversationLog(tmp_path)
    log.clear(404)  # must not raise
    assert log.read_conversation(404) == []


def test_test_flag_is_stamped_on_records_and_defaults_false(tmp_path: Path):
    # Synthetic conversations are flagged so the monitor UI can badge them as tests.
    log = ConversationLog(tmp_path)
    log.append(1, "user", "real", ts="2026-06-21T10:00:00+00:00")
    log.append("synthetic-x", "user", "fake", test=True, ts="2026-06-21T10:00:00+00:00")

    real = log.read_conversation(1)
    assert real[0].get("test") in (None, False)  # live records carry no test flag
    synth = log.read_conversation("synthetic-x")
    assert synth[0]["test"] is True


def test_list_users_surfaces_the_test_flag(tmp_path: Path):
    log = ConversationLog(tmp_path)
    log.append(1, "user", "real", name="Ana", ts="2026-06-21T10:00:00+00:00")
    log.append("synthetic-x", "user", "fake", name="Sim Runner", test=True,
               ts="2026-06-21T11:00:00+00:00")

    by_id = {u["user_id"]: u for u in log.list_users()}
    assert by_id["1"]["test"] is False
    assert by_id["synthetic-x"]["test"] is True
