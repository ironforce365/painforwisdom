"""Proactive outreach: decide when the coach should re-engage a quiet user.

Two cadences, per the product spec:
  * **Inactivity** — after ~1 day with no user message, reach out at *some random
    point during the following day*.
  * **Open loop** — if the coach's last message left something to circle back on
    ("tell me how it goes"), don't wait a full day; a couple of hours is enough.

The decision (`decide`) is a PURE function: the clock (`now`), randomness (`rng`)
and the open-loop classifier (`is_open_loop`) are all injected, so it is fully
deterministic and unit-tested. The random send time is committed once (stored in
`due_at`) so it stays stable across scheduler ticks instead of being re-rolled —
"some point during the day" is a single chosen moment, not a fresh dice roll every
scan.

`OutreachStore` persists per-user state to a single JSON file on the shared
`coach_state` volume (atomic .tmp + os.replace, like the quota/welcome stores).
"""
from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass, replace
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


@dataclass(frozen=True)
class OutreachConfig:
    """Timing policy. Defaults: 1-day inactivity bar, 2-hour open-loop bar, and a
    nighttime quiet window of [22:00, 09:00) in ``tz``."""
    inactivity_after: timedelta = timedelta(hours=24)
    inactivity_window: timedelta = timedelta(hours=24)
    followup_after: timedelta = timedelta(hours=2)
    followup_window: timedelta = timedelta(hours=2)
    quiet_start: time = time(22, 0)  # outreach not allowed at/after this local time
    quiet_end: time = time(9, 0)     # ...until this local time
    tz: str = "UTC"


@dataclass
class UserOutreach:
    """Per-user outreach bookkeeping. Timestamps are tz-aware UTC."""
    last_user_ts: datetime | None = None
    last_coach_ts: datetime | None = None
    last_outreach_ts: datetime | None = None
    due_at: datetime | None = None
    last_coach_text: str = ""
    language_code: str | None = None


@dataclass(frozen=True)
class Decision:
    should_outreach: bool
    kind: str | None = None       # "inactivity" | "followup" | None
    due_at: datetime | None = None  # committed send time to persist (None = clear)
    reason: str = ""


# Forward-commitment cues that mark a genuine "go do this and circle back" open
# loop — deliberately NOT plain questions (a coach ends most turns with one, so
# matching "?" would fire the short window on everyone). English + Spanish for the
# pilot. This is the conservative default classifier; `decide` takes `is_open_loop`
# as a seam so an LLM classifier can replace it later without touching the engine.
_OPEN_LOOP_CUES = (
    "let me know", "tell me how", "how it goes", "how it went", "how that goes",
    "how that feels", "report back", "keep me posted", "circle back",
    "next time you", "next time we", "come back and", "after your", "after you ",
    "once you've", "once you ", "give it a", "see how it",
    # Spanish
    "avísame", "avisame", "cuéntame", "cuentame", "dime cómo", "dime como",
    "la próxima", "la proxima", "ya me dices", "me cuentas",
)


def is_open_loop_heuristic(text: str) -> bool:
    """Conservative keyword classifier: True only when the coach's last message
    contains an explicit forward-looking commitment (the user is expected to go
    do something and report back), not merely a reflective question."""
    low = (text or "").lower()
    return any(cue in low for cue in _OPEN_LOOP_CUES)


def _zone(tz_name: str) -> ZoneInfo:
    try:
        return ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, ValueError, KeyError):
        return ZoneInfo("UTC")


def _in_quiet_hours(now: datetime, config: OutreachConfig) -> bool:
    """True if ``now`` (aware UTC) falls in the quiet window of ``tz``.

    The window runs [quiet_start, quiet_end) and may wrap midnight (the default
    22:00→09:00 does); a same-day window (e.g. 01:00→06:00) works too, and
    quiet_start == quiet_end means no quiet hours at all."""
    local = now.astimezone(_zone(config.tz)).timetz().replace(tzinfo=None)
    if config.quiet_start <= config.quiet_end:  # same-day window (empty if equal)
        return config.quiet_start <= local < config.quiet_end
    return local >= config.quiet_start or local < config.quiet_end


def decide(
    state: UserOutreach,
    now: datetime,
    *,
    rng: Callable[[], float],
    is_open_loop: Callable[[str], bool],
    config: OutreachConfig,
) -> Decision:
    """Decide whether to reach out to one user right now.

    Returns a `Decision` whose `due_at` is the committed send time the caller
    should persist (``None`` means clear it). `should_outreach=True` means send
    now (the caller records the outreach and clears `due_at`).
    """
    # Never proactively contact a user who has never spoken to the coach.
    if state.last_user_ts is None:
        return Decision(False, due_at=None)

    # No-pester: we already reached out *after* their last message and they have
    # not replied. Stay silent (clear the committed time) until they message again.
    if state.last_outreach_ts is not None and state.last_outreach_ts > state.last_user_ts:
        return Decision(False, due_at=None, reason="awaiting-reply")

    # Did the coach speak last, and did it leave an open loop to circle back on?
    coach_last = (
        state.last_coach_ts is not None and state.last_coach_ts >= state.last_user_ts
    )
    open_loop = (
        coach_last
        and bool(state.last_coach_text)
        and is_open_loop(state.last_coach_text)
    )
    if open_loop:
        after, window, kind = config.followup_after, config.followup_window, "followup"
    else:
        after, window, kind = config.inactivity_after, config.inactivity_window, "inactivity"

    eligible_at = state.last_user_ts + after
    if now < eligible_at:
        # Not idle long enough — nothing scheduled yet.
        return Decision(False, due_at=None)

    # Commit a random send time within the window, once. Recompute only if missing
    # or stale (left over from a longer prior window).
    due_at = state.due_at
    if due_at is None or due_at < eligible_at:
        offset = rng() * window.total_seconds()
        due_at = eligible_at + timedelta(seconds=offset)

    if now < due_at:
        return Decision(False, kind=kind, due_at=due_at, reason="scheduled")

    if _in_quiet_hours(now, config):
        # Hold (keep the committed time) until the quiet window ends.
        return Decision(False, kind=kind, due_at=due_at, reason="quiet-hours")

    return Decision(True, kind=kind, due_at=due_at, reason="due")


# ---------------------------------------------------------------------------

def _to_iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def _from_iso(s: str | None) -> datetime | None:
    return datetime.fromisoformat(s) if s else None


class OutreachStore:
    """Persisted per-user outreach state, JSON on the shared coach_state volume."""

    def __init__(self, path: Path):
        self._path = Path(path)
        self._lock = threading.Lock()
        self._users: dict[str, UserOutreach] = {}
        if self._path.exists():
            data = json.loads(self._path.read_text(encoding="utf-8"))
            for uid, rec in data.get("users", {}).items():
                self._users[uid] = UserOutreach(
                    last_user_ts=_from_iso(rec.get("last_user_ts")),
                    last_coach_ts=_from_iso(rec.get("last_coach_ts")),
                    last_outreach_ts=_from_iso(rec.get("last_outreach_ts")),
                    due_at=_from_iso(rec.get("due_at")),
                    last_coach_text=rec.get("last_coach_text", ""),
                    language_code=rec.get("language_code"),
                )

    def get(self, user_id) -> UserOutreach:
        """Return a snapshot COPY of the user's state. Callers use it to compare
        against later reads (e.g. the outreach scan's mid-generation re-check),
        so it must not alias the live object that concurrent records mutate."""
        with self._lock:
            s = self._users.get(str(user_id))
            return replace(s) if s is not None else UserOutreach()

    def all_users(self) -> list[str]:
        with self._lock:
            return list(self._users.keys())

    def record_user_message(
        self, user_id, ts: datetime, *, language_code: str | None = None
    ) -> None:
        """A new user message: refresh activity and cancel any scheduled outreach
        (the user is engaged again; the cycle restarts from here). Remembers the
        user's language so a later outreach can be written in it."""
        with self._lock:
            s = self._users.setdefault(str(user_id), UserOutreach())
            s.last_user_ts = ts
            s.due_at = None
            if language_code:
                s.language_code = language_code
            self._persist()

    def record_coach_message(
        self, user_id, ts: datetime, text: str, *, is_outreach: bool = False
    ) -> None:
        """The coach spoke. Records the last coach text (for open-loop detection).
        When this is a proactive outreach, stamps `last_outreach_ts` and clears the
        committed `due_at`."""
        with self._lock:
            s = self._users.setdefault(str(user_id), UserOutreach())
            s.last_coach_ts = ts
            s.last_coach_text = text or ""
            if is_outreach:
                s.last_outreach_ts = ts
                s.due_at = None
            self._persist()

    def record_outreach_attempt(self, user_id, ts: datetime) -> None:
        """An outreach was attempted but nothing (or nothing verifiable) reached
        the user — send failure, empty composed reply, post-send bookkeeping
        error. Still counts as this silence's one attempt (no-pester): stamping
        `last_outreach_ts` stops the scheduler from re-generating/re-sending
        every tick until the user speaks again. `last_coach_text` is left alone —
        no message reached them, so open-loop context is unchanged."""
        with self._lock:
            s = self._users.setdefault(str(user_id), UserOutreach())
            s.last_outreach_ts = ts
            s.due_at = None
            self._persist()

    def set_due_at(self, user_id, due_at: datetime | None) -> None:
        with self._lock:
            s = self._users.get(str(user_id)) or UserOutreach()
            if s.due_at == due_at:  # no change → skip the disk write (runs per tick)
                return
            self._users[str(user_id)] = s
            s.due_at = due_at
            self._persist()

    def clear(self, user_id) -> None:
        """Forget a user entirely (used by /restart)."""
        with self._lock:
            if self._users.pop(str(user_id), None) is not None:
                self._persist()

    def _persist(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "version": 1,
            "users": {
                uid: {
                    "last_user_ts": _to_iso(s.last_user_ts),
                    "last_coach_ts": _to_iso(s.last_coach_ts),
                    "last_outreach_ts": _to_iso(s.last_outreach_ts),
                    "due_at": _to_iso(s.due_at),
                    "last_coach_text": s.last_coach_text,
                    "language_code": s.language_code,
                }
                for uid, s in self._users.items()
            },
        }
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(tmp, self._path)
