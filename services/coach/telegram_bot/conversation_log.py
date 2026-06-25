"""Append-only per-user conversation log for the monitoring UI.

One JSONL file per user (``<root>/<user_id>.jsonl``), one record per line:
``{"ts", "role", "text", "name"}``. The Telegram bot is the writer (it's the
only layer that knows the user's display name); the monitor web service is the
reader. Reads are byte-capped so a huge history is tailed rather than loaded
whole into the browser."""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_MAX_BYTES = 5 * 1024 * 1024  # 5MB read cap (per the UI spec)


class ConversationLog:
    def __init__(self, root: Path):
        self._root = Path(root)
        self._lock = threading.Lock()

    def _file(self, user_id) -> Path:
        return self._root / f"{user_id}.jsonl"

    def append(self, user_id, role: str, text: str, *, name: str | None = None,
               ts: str | None = None, test: bool = False) -> None:
        record = {
            "ts": ts or datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "role": role,
            "text": text,
            "name": name,
        }
        if test:
            # Only stamped on synthetic-harness conversations, so the monitor UI can
            # badge them and live records stay byte-identical to before.
            record["test"] = True
        line = json.dumps(record, ensure_ascii=False) + "\n"
        with self._lock:
            self._root.mkdir(parents=True, exist_ok=True)
            with self._file(user_id).open("a", encoding="utf-8") as fh:
                fh.write(line)

    def read_conversation(self, user_id, *, max_bytes: int = DEFAULT_MAX_BYTES) -> list[dict]:
        """Return this user's messages, keeping only the last ``max_bytes`` on disk.

        When truncated we drop the (possibly partial) first surviving line so every
        returned record is a complete, parseable JSON object."""
        path = self._file(user_id)
        if not path.exists():
            return []
        size = path.stat().st_size
        with path.open("rb") as fh:
            if size > max_bytes:
                fh.seek(size - max_bytes)
                raw = fh.read()
                # First line may be cut mid-record — discard it.
                raw = raw.split(b"\n", 1)[1] if b"\n" in raw else b""
            else:
                raw = fh.read()
        out = []
        for line in raw.decode("utf-8", errors="ignore").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return out

    def clear(self, user_id) -> None:
        """Delete this user's conversation history (used by /restart). No-op if
        the user has no log yet."""
        with self._lock:
            self._file(user_id).unlink(missing_ok=True)

    def list_users(self) -> list[dict]:
        """One summary per user: id, latest known name, last message ts/text/role.

        Sorted most-recently-active first."""
        if not self._root.exists():
            return []
        summaries = []
        for path in self._root.glob("*.jsonl"):
            records = self.read_conversation(path.stem)
            if not records:
                continue
            last = records[-1]
            name = next(
                (r.get("name") for r in reversed(records) if r.get("name")), None
            )
            summaries.append({
                "user_id": path.stem,
                "name": name,
                "last_ts": last.get("ts", ""),
                "last_text": last.get("text", ""),
                "last_role": last.get("role", ""),
                "message_count": len(records),
                # A conversation is a test if any of its records is flagged.
                "test": any(r.get("test") for r in records),
            })
        summaries.sort(key=lambda s: s["last_ts"], reverse=True)
        return summaries
