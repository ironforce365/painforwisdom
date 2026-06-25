"""The bot-side outreach scan: walk the outreach store, decide per user, and for
due users call the coach, send the message, log it, and mark the outreach.

Everything external (allowlist, coach client, send, conversation log) is faked so
the scan's orchestration is tested without a live bot or coach."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from telegram_bot.bot import scan_and_outreach
from telegram_bot.outreach import OutreachConfig, OutreachStore

UTC = timezone.utc
NOON = datetime(2026, 6, 20, 12, 0, tzinfo=UTC)
CFG = OutreachConfig()
MID = lambda: 0.5
NO_OPEN = lambda _t: False


class FakeAllowlist:
    def __init__(self, allowed=True):
        self._allowed = allowed

    def allowed(self, uid):
        return self._allowed


class FakeCoach:
    def __init__(self, reply="hey, still here when you're ready", raises=False):
        self.reply = reply
        self.raises = raises
        self.calls = []

    def outreach(self, user_id, *, kind, language_code=None, last_coach_text=None):
        self.calls.append((user_id, kind, language_code, last_coach_text))
        if self.raises:
            raise RuntimeError("coach down")
        return {"reply": self.reply, "crisis": False}


class FakeConvo:
    def __init__(self):
        self.appended = []

    def append(self, user_id, role, text, **kw):
        self.appended.append((user_id, role, text))


async def _scan(store, coach, *, allowlist=None, convo=None, now=NOON):
    sent_msgs = []

    async def send(uid, text):
        sent_msgs.append((uid, text))

    n = await scan_and_outreach(
        store=store,
        allowlist=allowlist or FakeAllowlist(),
        coach=coach,
        convo=convo or FakeConvo(),
        send_message=send,
        now=now,
        rng=MID,
        is_open_loop=NO_OPEN,
        config=CFG,
    )
    return n, sent_msgs


@pytest.mark.asyncio
async def test_due_user_is_contacted_logged_and_marked(tmp_path: Path):
    store = OutreachStore(tmp_path / "o.json")
    # Quiet >24h, with a committed past-due send time.
    last = NOON - timedelta(hours=40)
    store.record_user_message("42", last, language_code="es")
    store.set_due_at("42", last + timedelta(hours=30))
    convo = FakeConvo()
    coach = FakeCoach()

    n, sent = await _scan(store, coach, convo=convo)

    assert n == 1
    assert sent == [("42", "hey, still here when you're ready")]
    assert coach.calls[0][2] == "es"  # language threaded through
    assert convo.appended == [(42, "coach", "hey, still here when you're ready")]
    s = store.get("42")
    assert s.last_outreach_ts == NOON   # marked, so no-pester kicks in next tick
    assert s.due_at is None             # committed time cleared after sending


@pytest.mark.asyncio
async def test_not_yet_due_user_is_not_contacted_but_time_committed(tmp_path: Path):
    store = OutreachStore(tmp_path / "o.json")
    store.record_user_message("42", NOON - timedelta(hours=25))  # just over the bar
    coach = FakeCoach()

    n, sent = await _scan(store, coach)

    assert n == 0
    assert coach.calls == []
    assert store.get("42").due_at is not None  # a send time is now scheduled


@pytest.mark.asyncio
async def test_non_allowlisted_user_is_skipped(tmp_path: Path):
    store = OutreachStore(tmp_path / "o.json")
    store.record_user_message("42", NOON - timedelta(hours=40))
    store.set_due_at("42", NOON - timedelta(hours=1))
    coach = FakeCoach()

    n, sent = await _scan(store, coach, allowlist=FakeAllowlist(allowed=False))

    assert n == 0
    assert coach.calls == []


@pytest.mark.asyncio
async def test_coach_failure_does_not_crash_the_scan(tmp_path: Path):
    store = OutreachStore(tmp_path / "o.json")
    store.record_user_message("42", NOON - timedelta(hours=40))
    store.set_due_at("42", NOON - timedelta(hours=1))
    coach = FakeCoach(raises=True)

    n, sent = await _scan(store, coach)

    assert n == 0
    assert sent == []
    # Not marked as reached-out → it'll be retried on a later tick.
    assert store.get("42").last_outreach_ts is None


@pytest.mark.asyncio
async def test_empty_reply_is_not_sent(tmp_path: Path):
    store = OutreachStore(tmp_path / "o.json")
    store.record_user_message("42", NOON - timedelta(hours=40))
    store.set_due_at("42", NOON - timedelta(hours=1))
    coach = FakeCoach(reply="   ")

    n, sent = await _scan(store, coach)

    assert n == 0
    assert sent == []
