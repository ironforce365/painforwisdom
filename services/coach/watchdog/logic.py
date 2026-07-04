"""Watchdog decision logic: probe results → heal/alert actions.

Pure and I/O-free so it is fully unit-testable; watchdog/runner.py owns the
probing (HTTP + docker) and the execution (compose restarts, Telegram alerts).

Policy (see tests/test_watchdog.py):
- a check must fail FAIL_THRESHOLD consecutive cycles before healing — single
  blips are tolerated;
- DNS failure inside the agent heals the WHOLE stack: every container captured
  the same host-resolver state at start time (the 2026-07-01→04 outage), so a
  stale resolver is never a one-container problem;
- after a heal, a cooldown gives the restart time to take before re-judging;
- restarts are capped per incident, then a single escalation alert asks for a
  human and the watchdog stands down until the stack recovers;
- the dead-bot heal is a force-recreate: a plain restart provably does not fix
  the post-host-crash docker log black hole.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

# compose service names, matching docker-compose.yml. mem0-postgres is
# intentionally not restarted by the watchdog: it holds state, has its own
# docker healthcheck, and every observed failure mode was in the stateless tier.
ALL_SERVICES = ["coach-agent", "telegram-bot", "mem0-api", "monitor"]

FAIL_THRESHOLD = 2  # consecutive failing cycles before healing


@dataclass
class Probe:
    """One probe cycle. None = unknown (e.g. agent unreachable → its deep-health
    body, which carries api_dns/mem0, was never seen)."""

    agent_http: bool
    api_dns: Optional[bool]
    mem0: Optional[bool]
    bot_alive: bool


@dataclass
class Action:
    kind: str  # "restart" | "recreate" | "alert"
    services: list[str] = field(default_factory=list)
    message: str = ""


@dataclass
class Decision:
    actions: list[Action] = field(default_factory=list)


# check name -> (heal kind, services to heal), in priority order: the broadest
# heal wins when several checks are over threshold (a DNS heal restarts the
# agent anyway, so a narrower simultaneous heal would be redundant).
_HEAL_PLAN: list[tuple[str, str, list[str]]] = [
    ("api_dns", "restart", list(ALL_SERVICES)),
    ("agent_http", "restart", ["coach-agent"]),
    ("mem0", "restart", ["mem0-api"]),
    ("bot_alive", "recreate", ["telegram-bot"]),
]


class WatchdogState:
    """Mutable counters carried across cycles by the runner loop."""

    def __init__(
        self,
        fail_threshold: int = FAIL_THRESHOLD,
        cooldown_cycles: int = 3,
        max_restarts_per_incident: int = 3,
    ):
        self.fail_threshold = fail_threshold
        self.cooldown_cycles = cooldown_cycles
        self.max_restarts_per_incident = max_restarts_per_incident
        self.fail_counts: dict[str, int] = {}
        self.cooldown = 0
        self.restarts_this_incident = 0
        self.incident_open = False
        self.escalated = False

    def _reset_incident(self) -> None:
        self.fail_counts.clear()
        self.cooldown = 0
        self.restarts_this_incident = 0
        self.incident_open = False
        self.escalated = False


def _failing_checks(probe: Probe) -> dict[str, bool]:
    """check name -> failing? (None/unknown does not count as failing)."""
    return {
        "api_dns": probe.api_dns is False,
        "agent_http": probe.agent_http is False,
        "mem0": probe.mem0 is False,
        "bot_alive": probe.bot_alive is False,
    }


def decide(state: WatchdogState, probe: Probe) -> Decision:
    """Advance one cycle: update counters, emit heal/alert actions."""
    failing = _failing_checks(probe)

    if not any(failing.values()):
        if state.incident_open:
            state._reset_incident()
            return Decision(
                actions=[Action(kind="alert", message="coach watchdog: stack recovered — all checks green")]
            )
        state.fail_counts.clear()
        return Decision()

    # Update consecutive-failure counters (passing checks reset to zero).
    for check, is_failing in failing.items():
        state.fail_counts[check] = state.fail_counts.get(check, 0) + 1 if is_failing else 0

    # A recent heal gets time to take effect before we judge again.
    if state.cooldown > 0:
        state.cooldown -= 1
        return Decision()

    # Broadest heal wins (plan is in priority order).
    for check, kind, services in _HEAL_PLAN:
        if state.fail_counts.get(check, 0) >= state.fail_threshold:
            if state.restarts_this_incident >= state.max_restarts_per_incident:
                if state.escalated:
                    return Decision()  # already asked for a human; stand down
                state.escalated = True
                return Decision(
                    actions=[
                        Action(
                            kind="alert",
                            message=(
                                f"coach watchdog: {check} still failing after "
                                f"{state.restarts_this_incident} heals — manual intervention needed"
                            ),
                        )
                    ]
                )
            state.restarts_this_incident += 1
            state.cooldown = state.cooldown_cycles
            state.incident_open = True
            verb = "force-recreating" if kind == "recreate" else "restarting"
            return Decision(
                actions=[
                    Action(kind=kind, services=list(services)),
                    Action(
                        kind="alert",
                        message=(
                            f"coach watchdog: {check} failing {state.fail_counts[check]} cycles — "
                            f"{verb} {', '.join(services)} "
                            f"(heal {state.restarts_this_incident}/{state.max_restarts_per_incident})"
                        ),
                    ),
                ]
            )

    return Decision()  # failing, but under threshold: tolerate the blip
