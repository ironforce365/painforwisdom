"""Synthetic runner: drives a profile through N turns against the coach over the
test channel, logging every exchange to the conversation log (flagged test=True)
so it shows in the monitor UI but never feeds the inbox. Supports many concurrent
personas (scalability) and arbitrarily long runs (>100 turns)."""
from __future__ import annotations

import threading

import pytest

from synthetic.profiles import Profile
from synthetic.runner import run_many, run_profile

PROF = Profile(slug="dana", name="Dana (test)", persona="anxious marathoner",
               style="short", opener="am I ready?", turn_count=3)


class FakeCoach:
    def __init__(self):
        self.calls = []
        self._lock = threading.Lock()

    def turn(self, user_id, text, language_code=None, channel="live"):
        with self._lock:
            self.calls.append((user_id, text, channel))
        return {"reply": f"coach to {user_id}: {text[:12]}", "crisis": False}


class FakeConvo:
    def __init__(self):
        self.records = []
        self._lock = threading.Lock()

    def append(self, user_id, role, text, *, name=None, test=False, ts=None):
        with self._lock:
            self.records.append((str(user_id), role, test))


def _counter_reply():
    box = {"n": 0}

    def reply(profile, history):
        box["n"] += 1
        return f"follow-up {box['n']}"

    return reply


def test_run_profile_drives_turns_on_the_test_channel():
    coach, convo = FakeCoach(), FakeConvo()
    result = run_profile(PROF, coach=coach, convo=convo, reply_fn=_counter_reply())

    # turn_count exchanges happened, all on the test channel.
    assert len(coach.calls) == 3
    assert all(c[2] == "test" for c in coach.calls)
    # First user message is the opener; later ones come from reply_fn.
    assert coach.calls[0][1] == "am I ready?"
    assert coach.calls[1][1] == "follow-up 1"
    # Every logged record is flagged test, under the namespaced synthetic id.
    assert len(convo.records) == 6  # 3 user + 3 coach
    assert all(test is True for (_uid, _role, test) in convo.records)
    assert all(uid == "synthetic-dana" for (uid, _r, _t) in convo.records)
    assert result["user_id"] == "synthetic-dana"
    assert result["turns"] == 3


def test_run_profile_survives_a_driver_failure():
    coach, convo = FakeCoach(), FakeConvo()

    def flaky_reply(profile, history):
        raise RuntimeError("CLI flaked")

    # Should not raise — a driver hiccup substitutes a fallback and the run goes on.
    result = run_profile(PROF, coach=coach, convo=convo, reply_fn=flaky_reply)
    assert len(coach.calls) == 3
    assert result["turns"] == 3


def test_long_conversation_runs_past_100_turns():
    long_prof = Profile(slug="endur", name="E", persona="p", style="", opener="go",
                        turn_count=120)
    coach, convo = FakeCoach(), FakeConvo()
    result = run_profile(long_prof, coach=coach, convo=convo, reply_fn=_counter_reply())
    assert result["turns"] == 120
    assert len(coach.calls) == 120


def test_run_many_drives_each_profile_with_its_own_user_id():
    profiles = [
        Profile(slug=f"p{i}", name=f"P{i}", persona="x", style="", opener="hi",
                turn_count=2)
        for i in range(5)
    ]
    convo = FakeConvo()
    coaches = []

    def make_coach():
        c = FakeCoach()
        coaches.append(c)
        return c

    results = run_many(profiles, make_coach=make_coach, convo=convo,
                       reply_fn=_counter_reply(), concurrency=3)

    assert len(results) == 5
    uids = {r["user_id"] for r in results}
    assert uids == {f"synthetic-p{i}" for i in range(5)}
    # 5 profiles × 2 turns × 2 roles = 20 logged records, all test-flagged.
    assert len(convo.records) == 20
    assert all(test is True for (_u, _r, test) in convo.records)
