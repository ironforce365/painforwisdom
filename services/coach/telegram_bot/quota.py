"""Per-user daily message quota with a midnight reset.

Each inbound user message consumes one unit; once a user hits ``limit`` for the
day they're blocked until the date rolls over. State is a single JSON file
(atomic .tmp + os.replace, like Allowlist) keyed on the active date; when the
caller passes a date newer than the stored one, all counters reset.

The current date is computed by ``local_today`` in a configurable timezone, so
'midnight' is well-defined and the reset is deterministic/testable."""
from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


@dataclass(frozen=True)
class QuotaResult:
    allowed: bool
    count: int
    limit: int


def local_today(now_utc: datetime, tz_name: str) -> date:
    """The calendar date 'now' falls on in ``tz_name`` (unknown tz → UTC)."""
    try:
        tz = ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, ValueError, KeyError):
        return now_utc.date()
    return now_utc.astimezone(tz).date()


class DailyQuota:
    def __init__(self, path: Path, *, limit: int = 100):
        self._path = Path(path)
        self._limit = limit
        self._lock = threading.Lock()
        self._date: str = ""
        self._counts: dict[str, int] = {}
        if self._path.exists():
            data = json.loads(self._path.read_text(encoding="utf-8"))
            self._date = data.get("date", "")
            self._counts = dict(data.get("counts", {}))

    def check_and_increment(self, user_id: int, today: date) -> QuotaResult:
        """Consume one message for ``user_id``. Blocks (no increment) at the limit."""
        with self._lock:
            iso = today.isoformat()
            if self._date != iso:  # date rolled over (or first ever) → reset
                self._date = iso
                self._counts = {}
            key = str(user_id)
            count = self._counts.get(key, 0)
            if count >= self._limit:
                return QuotaResult(allowed=False, count=count, limit=self._limit)
            count += 1
            self._counts[key] = count
            self._persist()
            return QuotaResult(allowed=True, count=count, limit=self._limit)

    def _persist(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        data = {"date": self._date, "limit": self._limit, "counts": self._counts}
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(tmp, self._path)
