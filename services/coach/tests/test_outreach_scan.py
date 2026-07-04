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
    # A GENERATION failure is transient (coach down / mid-deploy): not marked as
    # reached-out → it'll be retried on a later tick.
    assert store.get("42").last_outreach_ts is None


@pytest.mark.asyncio
async def test_empty_reply_is_suppressed_not_retried_forever(tmp_path: Path):
    # An empty composed reply is a dead end for this silence: without stamping
    # the attempt, the past-due due_at would trigger a full ~90s regeneration on
    # every tick indefinitely.
    store = OutreachStore(tmp_path / "o.json")
    store.record_user_message("42", NOON - timedelta(hours=40))
    store.set_due_at("42", NOON - timedelta(hours=1))
    coach = FakeCoach(reply="   ")

    n, sent = await _scan(store, coach)

    assert n == 0
    assert sent == []
    assert store.get("42").last_outreach_ts == NOON  # attempt stamped (no-pester)
    # Next tick: no second generation.
    n2, _ = await _scan(store, coach, now=NOON + timedelta(minutes=30))
    assert len(coach.calls) == 1


@pytest.mark.asyncio
async def test_send_failure_is_suppressed_not_retried_forever(tmp_path: Path):
    # e.g. the user blocked the bot → telegram raises on send. Never-spam beats
    # delivery: stamp the attempt so the scheduler stops regenerating every tick.
    store = OutreachStore(tmp_path / "o.json")
    store.record_user_message("42", NOON - timedelta(hours=40))
    store.set_due_at("42", NOON - timedelta(hours=1))
    coach = FakeCoach()

    async def failing_send(uid, text):
        raise RuntimeError("Forbidden: bot was blocked by the user")

    n = await scan_and_outreach(
        store=store, allowlist=FakeAllowlist(), coach=coach, convo=FakeConvo(),
        send_message=failing_send, now=NOON, rng=MID, is_open_loop=NO_OPEN, config=CFG,
    )
    assert n == 0
    assert store.get("42").last_outreach_ts == NOON
    # Next tick is a no-op for this user (no second generation).
    await _scan(store, coach, now=NOON + timedelta(minutes=30))
    assert len(coach.calls) == 1


@pytest.mark.asyncio
async def test_convo_log_failure_after_send_does_not_resend(tmp_path: Path):
    # The no-pester stamp must land BEFORE the fallible conversation-log append:
    # a monitor-log error after a delivered message must not cause a duplicate
    # send on the next tick.
    store = OutreachStore(tmp_path / "o.json")
    store.record_user_message("42", NOON - timedelta(hours=40))
    store.set_due_at("42", NOON - timedelta(hours=1))
    coach = FakeCoach()

    class ExplodingConvo:
        def append(self, *a, **kw):
            raise OSError("disk full")

    sent_msgs = []

    async def send(uid, text):
        sent_msgs.append(uid)

    n = await scan_and_outreach(
        store=store, allowlist=FakeAllowlist(), coach=coach, convo=ExplodingConvo(),
        send_message=send, now=NOON, rng=MID, is_open_loop=NO_OPEN, config=CFG,
    )
    assert n == 1  # delivered and counted despite the log failure
    assert store.get("42").last_outreach_ts == NOON
    # Next tick: nothing re-sent.
    await _scan(store, coach, now=NOON + timedelta(minutes=30))
    assert sent_msgs == ["42"]
    assert len(coach.calls) == 1


@pytest.mark.asyncio
async def test_user_message_mid_generation_drops_the_stale_nudge(tmp_path: Path):
    # The user finally messages while their outreach is being generated (~90s):
    # sending the "you've gone quiet" nudge right after their message would read
    # as the bot ignoring them — it must be dropped.
    store = OutreachStore(tmp_path / "o.json")
    store.record_user_message("42", NOON - timedelta(hours=40))
    store.set_due_at("42", NOON - timedelta(hours=1))

    class RacingCoach(FakeCoach):
        def outreach(self, user_id, **kw):
            # Simulate the user's message landing while generation is in flight.
            store.record_user_message(user_id, NOON)
            return super().outreach(user_id, **kw)

    coach = RacingCoach()
    n, sent = await _scan(store, coach)

    assert n == 0
    assert sent == []
    assert store.get("42").last_outreach_ts is None  # nothing was attempted at them
