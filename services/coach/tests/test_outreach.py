"""Proactive outreach: per-user state store + the pure decision engine.

The engine decides whether the coach should re-engage a quiet user. It is pure
and deterministic — the clock (`now`), randomness (`rng`) and the open-loop
classifier (`is_open_loop`) are all injected — so every branch is unit-tested
without a real bot, real time, or a real LLM."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from telegram_bot.outreach import (
    OutreachConfig,
    OutreachStore,
    UserOutreach,
    decide,
    is_open_loop_heuristic,
)

UTC = timezone.utc
T0 = datetime(2026, 6, 20, 12, 0, tzinfo=UTC)  # noon UTC, comfortably inside [09,22)

CFG = OutreachConfig()  # defaults: 24h inactivity / 2h followup, quiet [22:00,09:00)

NO_OPEN = lambda _t: False
ALWAYS_OPEN = lambda _t: True
MID = lambda: 0.5  # deterministic midpoint of any window


def _state(**kw) -> UserOutreach:
    base = dict(last_user_ts=None, last_coach_ts=None, last_outreach_ts=None,
                due_at=None, last_coach_text="")
    base.update(kw)
    return UserOutreach(**base)


# ---- decision engine -------------------------------------------------------

def test_never_engaged_user_is_never_contacted():
    d = decide(_state(), T0, rng=MID, is_open_loop=NO_OPEN, config=CFG)
    assert d.should_outreach is False


def test_recent_activity_no_outreach():
    # User messaged 1h ago — nowhere near the 24h inactivity bar.
    s = _state(last_user_ts=T0 - timedelta(hours=1))
    d = decide(s, T0, rng=MID, is_open_loop=NO_OPEN, config=CFG)
    assert d.should_outreach is False
    assert d.due_at is None  # not eligible → nothing committed yet


def test_inactivity_commits_a_random_send_time_in_the_next_day():
    # 25h since last user message → past the 24h bar but the committed send time
    # is a random point within the FOLLOWING 24h window, so not yet due.
    last = T0 - timedelta(hours=25)
    s = _state(last_user_ts=last)
    d = decide(s, T0, rng=MID, is_open_loop=NO_OPEN, config=CFG)
    assert d.should_outreach is False
    assert d.kind == "inactivity"
    # midpoint of [last+24h, last+48h]
    assert d.due_at == last + timedelta(hours=24) + timedelta(hours=12)


def test_inactivity_sends_once_due_time_passes():
    last = T0 - timedelta(hours=40)
    due = last + timedelta(hours=24) + timedelta(hours=12)  # = last+36h, before T0
    s = _state(last_user_ts=last, due_at=due)
    d = decide(s, T0, rng=MID, is_open_loop=NO_OPEN, config=CFG)
    assert d.should_outreach is True
    assert d.kind == "inactivity"


def test_committed_due_time_is_stable_across_ticks():
    # Two ticks an hour apart must keep the SAME committed send time (we pick the
    # random moment once, not anew every scan — else it'd bias toward the end).
    last = T0 - timedelta(hours=25)
    s = _state(last_user_ts=last)
    d1 = decide(s, T0, rng=lambda: 0.3, is_open_loop=NO_OPEN, config=CFG)
    s2 = _state(last_user_ts=last, due_at=d1.due_at)
    d2 = decide(s2, T0 + timedelta(hours=1), rng=lambda: 0.9, is_open_loop=NO_OPEN, config=CFG)
    assert d2.due_at == d1.due_at  # rng change ignored once committed


def test_open_loop_uses_the_short_two_hour_window():
    # Coach spoke last and left an open loop → eligible after 2h, send within the
    # next 2h — no waiting a full day.
    last = T0 - timedelta(hours=3)
    s = _state(last_user_ts=last,
               last_coach_ts=last + timedelta(seconds=30),
               last_coach_text="go try the box breathing and tell me how it goes")
    d = decide(s, T0, rng=MID, is_open_loop=ALWAYS_OPEN, config=CFG)
    assert d.kind == "followup"
    # midpoint of [last+2h, last+4h]
    assert d.due_at == last + timedelta(hours=2) + timedelta(hours=1)


def test_open_loop_sends_after_a_couple_hours_not_a_day():
    last = T0 - timedelta(hours=5)
    due = last + timedelta(hours=2) + timedelta(hours=1)  # last+3h < T0
    s = _state(last_user_ts=last,
               last_coach_ts=last + timedelta(seconds=30),
               last_coach_text="report back after your run",
               due_at=due)
    d = decide(s, T0, rng=MID, is_open_loop=ALWAYS_OPEN, config=CFG)
    assert d.should_outreach is True
    assert d.kind == "followup"


def test_no_pester_after_an_unanswered_outreach():
    # We already reached out (after the user's last message) and they haven't
    # replied → stay silent until they do; the committed time is cleared.
    last = T0 - timedelta(days=3)
    s = _state(last_user_ts=last,
               last_outreach_ts=last + timedelta(days=1),
               due_at=last + timedelta(hours=30))
    d = decide(s, T0, rng=MID, is_open_loop=NO_OPEN, config=CFG)
    assert d.should_outreach is False
    assert d.due_at is None


def test_quiet_hours_hold_the_send():
    # Due time has passed but it's 03:00 UTC — inside quiet hours → hold.
    night = datetime(2026, 6, 21, 3, 0, tzinfo=UTC)
    last = night - timedelta(hours=40)
    due = last + timedelta(hours=30)  # already past, but it's the middle of the night
    s = _state(last_user_ts=last, due_at=due)
    d = decide(s, night, rng=MID, is_open_loop=NO_OPEN, config=CFG)
    assert d.should_outreach is False
    assert d.due_at == due  # held, not discarded — fires when morning comes


def test_quiet_hours_respect_configured_timezone():
    # 06:00 UTC is 23:00 the previous day in LA (UTC-7 in June) → quiet in LA.
    cfg = OutreachConfig(tz="America/Los_Angeles")
    now = datetime(2026, 6, 21, 6, 0, tzinfo=UTC)
    last = now - timedelta(hours=40)
    due = last + timedelta(hours=30)
    s = _state(last_user_ts=last, due_at=due)
    d = decide(s, now, rng=MID, is_open_loop=NO_OPEN, config=cfg)
    assert d.should_outreach is False


def test_quiet_hours_support_a_non_wrapping_window():
    # A same-day window (01:00–06:00) must be quiet inside and allowed outside —
    # the naive or-predicate would treat this configuration as always-quiet.
    from datetime import time as dtime
    cfg = OutreachConfig(quiet_start=dtime(1, 0), quiet_end=dtime(6, 0))
    last = T0 - timedelta(hours=40)
    due = last + timedelta(hours=30)
    s = _state(last_user_ts=last, due_at=due)
    # 03:00 → inside the window → held.
    night = datetime(2026, 6, 21, 3, 0, tzinfo=UTC)
    assert decide(s, night, rng=MID, is_open_loop=NO_OPEN, config=cfg).should_outreach is False
    # 12:00 → outside the window → sends.
    noon = datetime(2026, 6, 21, 12, 0, tzinfo=UTC)
    assert decide(s, noon, rng=MID, is_open_loop=NO_OPEN, config=cfg).should_outreach is True


# ---- state store -----------------------------------------------------------

def test_store_records_and_persists_across_instances(tmp_path: Path):
    p = tmp_path / "outreach.json"
    store = OutreachStore(p)
    store.record_user_message("42", T0)
    store.record_coach_message("42", T0 + timedelta(seconds=5), "nice work — what's next?")
    # A fresh instance (restart) sees the persisted state.
    reloaded = OutreachStore(p)
    s = reloaded.get("42")
    assert s.last_user_ts == T0
    assert s.last_coach_text == "nice work — what's next?"
    assert "42" in reloaded.all_users()


def test_store_remembers_user_language(tmp_path: Path):
    # The outreach message must be in the user's language, so we stash it.
    p = tmp_path / "outreach.json"
    store = OutreachStore(p)
    store.record_user_message("42", T0, language_code="es")
    assert OutreachStore(p).get("42").language_code == "es"


def test_user_message_clears_pending_due_time(tmp_path: Path):
    # A new user message means they're engaged again → any scheduled outreach
    # is cancelled (the cycle restarts from this message).
    store = OutreachStore(tmp_path / "outreach.json")
    store.set_due_at("42", T0 + timedelta(hours=30))
    store.record_user_message("42", T0 + timedelta(hours=1))
    assert store.get("42").due_at is None


def test_record_outreach_marks_outreach_timestamp(tmp_path: Path):
    store = OutreachStore(tmp_path / "outreach.json")
    store.record_user_message("42", T0)
    store.record_coach_message("42", T0 + timedelta(days=2), "still here when you are",
                               is_outreach=True)
    s = store.get("42")
    assert s.last_outreach_ts == T0 + timedelta(days=2)
    assert s.due_at is None  # sending clears the committed time


def test_record_outreach_attempt_suppresses_without_touching_coach_text(tmp_path: Path):
    # A failed outreach (send error, empty reply) must still count as the one
    # attempt for this silence — no-pester semantics — without corrupting the
    # last_coach_text used for open-loop detection.
    store = OutreachStore(tmp_path / "o.json")
    store.record_user_message("42", T0)
    store.record_coach_message("42", T0 + timedelta(seconds=5), "tell me how it goes")
    store.set_due_at("42", T0 + timedelta(hours=30))

    store.record_outreach_attempt("42", T0 + timedelta(hours=31))

    s = store.get("42")
    assert s.last_outreach_ts == T0 + timedelta(hours=31)
    assert s.due_at is None
    assert s.last_coach_text == "tell me how it goes"  # untouched
    # And decide() now applies the no-pester rule.
    d = decide(s, T0 + timedelta(hours=32), rng=MID, is_open_loop=ALWAYS_OPEN, config=CFG)
    assert d.should_outreach is False


def test_clear_removes_a_user(tmp_path: Path):
    store = OutreachStore(tmp_path / "outreach.json")
    store.record_user_message("42", T0)
    store.clear("42")
    assert "42" not in store.all_users()


# ---- open-loop heuristic ---------------------------------------------------
# Conservative by design: a coach ends most turns with a question, so we must
# NOT treat every "?" as an open loop (that would fire the 2h path on everyone
# and collapse the 1-day path). Only explicit "go do this and come back" cues.

def test_heuristic_detects_explicit_forward_commitments():
    assert is_open_loop_heuristic("Go try the box breathing and tell me how it goes.")
    assert is_open_loop_heuristic("Report back after your long run on Sunday.")
    assert is_open_loop_heuristic("Let me know how that feels next time you're out.")
    assert is_open_loop_heuristic("Hazlo en tu próxima salida y avísame cómo te fue.")


def test_heuristic_ignores_plain_reflective_questions():
    assert not is_open_loop_heuristic("What do you think is holding you back?")
    assert not is_open_loop_heuristic("How are you feeling about the race today?")
    assert not is_open_loop_heuristic("")
