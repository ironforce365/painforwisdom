"""Watchdog decision logic — probe results → heal/alert actions.

The watchdog loop (watchdog/runner.py, host systemd unit) probes the stack
every cycle and feeds results into ``decide()``. This module pins the policy:

- transient blips don't restart anything (threshold = 2 consecutive failures)
- a DNS failure inside the agent restarts the WHOLE stack (every container is
  pinned to the same stale resolver — the 2026-07-01→04 outage)
- component failures restart only that component
- a silent telegram-bot (no log output) is force-recreated, not restarted —
  restart provably does not fix the post-host-crash log black hole
- restarts are capped per incident, then the watchdog alerts for a human and
  stands down until the stack recovers
- every heal action and every recovery fires exactly one alert
"""
from __future__ import annotations

from watchdog.logic import ALL_SERVICES, Probe, WatchdogState, decide


def _healthy() -> Probe:
    return Probe(agent_http=True, api_dns=True, mem0=True, bot_alive=True)


def _actions_of(decision, kind):
    return [a for a in decision.actions if a.kind == kind]


def test_healthy_probe_no_actions():
    st = WatchdogState()
    d = decide(st, _healthy())
    assert d.actions == []


def test_single_failure_is_tolerated():
    st = WatchdogState()
    d = decide(st, Probe(agent_http=False, api_dns=None, mem0=None, bot_alive=True))
    assert d.actions == []  # first strike: wait, could be a blip


def test_second_consecutive_agent_failure_restarts_agent():
    st = WatchdogState()
    probe = Probe(agent_http=False, api_dns=None, mem0=None, bot_alive=True)
    decide(st, probe)
    d = decide(st, probe)
    restarts = _actions_of(d, "restart")
    assert len(restarts) == 1
    assert restarts[0].services == ["coach-agent"]
    assert _actions_of(d, "alert")  # heal is always announced


def test_dns_failure_restarts_whole_stack():
    st = WatchdogState()
    probe = Probe(agent_http=True, api_dns=False, mem0=True, bot_alive=True)
    decide(st, probe)
    d = decide(st, probe)
    restarts = _actions_of(d, "restart")
    assert len(restarts) == 1
    assert set(restarts[0].services) == set(ALL_SERVICES)


def test_mem0_failure_restarts_only_mem0():
    st = WatchdogState()
    probe = Probe(agent_http=True, api_dns=True, mem0=False, bot_alive=True)
    decide(st, probe)
    d = decide(st, probe)
    restarts = _actions_of(d, "restart")
    assert len(restarts) == 1
    assert restarts[0].services == ["mem0-api"]


def test_dead_bot_is_recreated_not_restarted():
    st = WatchdogState()
    probe = Probe(agent_http=True, api_dns=True, mem0=True, bot_alive=False)
    decide(st, probe)
    d = decide(st, probe)
    recreates = _actions_of(d, "recreate")
    assert len(recreates) == 1
    assert recreates[0].services == ["telegram-bot"]
    assert _actions_of(d, "restart") == []


def test_recovery_resets_counters_and_alerts_once():
    st = WatchdogState()
    bad = Probe(agent_http=False, api_dns=None, mem0=None, bot_alive=True)
    decide(st, bad)
    decide(st, bad)  # heal fired here
    d_rec = decide(st, _healthy())
    alerts = _actions_of(d_rec, "alert")
    assert len(alerts) == 1
    assert "recovered" in alerts[0].message.lower()
    # Next healthy cycle: silence.
    assert decide(st, _healthy()).actions == []


def test_cooldown_no_second_restart_immediately_after_heal():
    st = WatchdogState()
    bad = Probe(agent_http=False, api_dns=None, mem0=None, bot_alive=True)
    decide(st, bad)
    d_heal = decide(st, bad)
    assert _actions_of(d_heal, "restart")
    # Container comes back slowly: the very next failing cycles must NOT
    # immediately restart again (give the heal time to take).
    d_next = decide(st, bad)
    assert _actions_of(d_next, "restart") == []


def test_restart_cap_then_escalate_to_human():
    st = WatchdogState(cooldown_cycles=0, max_restarts_per_incident=3)
    bad = Probe(agent_http=False, api_dns=None, mem0=None, bot_alive=True)
    restarts_seen = 0
    escalated = False
    for _ in range(12):
        d = decide(st, bad)
        restarts_seen += len(_actions_of(d, "restart"))
        if any("manual" in a.message.lower() for a in _actions_of(d, "alert")):
            escalated = True
    assert restarts_seen == 3  # capped
    assert escalated  # a human was asked for
