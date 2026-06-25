"""One-time backfill: vault ``_inbox`` markdown → monitor conversation log.

The monitoring UI reads the JSONL conversation log the bot started writing in
v0.1.12; it has no record of turns from before that. But every turn was already
persisted as a markdown file under ``_inbox/<user_id>/<timestamp>.md`` (see
agent/hooks.py). This script replays those into the conversation log so the UI
shows the full history.

Idempotent: it ``clear()``s each user's log before replaying, so re-running can't
duplicate. The vault inbox is read-only here — never modified. Synthetic test
users (smoke-test / perf-probe / healthcheck / numeric test ids) are skipped by
default.

Usage (from services/coach, with deps available):
    python -m scripts.backfill_conversations --inbox /vault/_inbox --out /state/conversations
"""
from __future__ import annotations

import argparse
import re
from datetime import datetime, timezone
from pathlib import Path

from telegram_bot.conversation_log import ConversationLog

# Directories under _inbox that aren't real pilot users.
DEFAULT_SKIP = {"smoke-test-v014", "perf-probe-v019", "__healthcheck_monitor__", "999001"}

_FRONTMATTER = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)


def parse_entry(text: str) -> tuple[str, str, str]:
    """Parse one inbox ``.md`` into ``(ts_iso, user_text, coach_text)``.

    The file shape is a YAML-ish frontmatter block (``user_id``, ``timestamp``,
    ``retrieved_sources``) followed by ``## User`` and ``## Coach`` sections."""
    ts_iso = ""
    m = _FRONTMATTER.match(text)
    if m:
        for line in m.group(1).splitlines():
            if line.startswith("timestamp:"):
                ts_iso = _to_iso(line.split(":", 1)[1].strip())
        body = text[m.end():]
    else:
        body = text
    user_text, coach_text = _split_sections(body)
    return ts_iso, user_text, coach_text


def _split_sections(body: str) -> tuple[str, str]:
    user_text, coach_text = "", ""
    if "## Coach" in body:
        before, coach_text = body.split("## Coach", 1)
    else:
        before = body
    if "## User" in before:
        user_text = before.split("## User", 1)[1]
    return user_text.strip(), coach_text.strip()


def _to_iso(stamp: str) -> str:
    """``20260622T110717Z`` → ``2026-06-22T11:07:17+00:00`` (best-effort)."""
    try:
        dt = datetime.strptime(stamp, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
        return dt.isoformat(timespec="seconds")
    except ValueError:
        return stamp


def backfill(inbox_root: Path, log: ConversationLog, *, skip: set[str] | None = None) -> dict:
    """Replay every user's inbox markdown into ``log``. Returns a per-user count."""
    skip = DEFAULT_SKIP if skip is None else skip
    inbox_root = Path(inbox_root)
    summary: dict[str, int] = {}
    if not inbox_root.exists():
        return summary
    for user_dir in sorted(p for p in inbox_root.iterdir() if p.is_dir()):
        uid = user_dir.name
        if uid in skip:
            continue
        log.clear(uid)  # idempotent re-run
        turns = 0
        for md in sorted(user_dir.glob("*.md")):
            ts, user_text, coach_text = parse_entry(md.read_text(encoding="utf-8"))
            if user_text:
                log.append(uid, "user", user_text, ts=ts or None)
            if coach_text:
                log.append(uid, "coach", coach_text, ts=ts or None)
            turns += 1
        if turns:
            summary[uid] = turns
    return summary


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--inbox", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    summary = backfill(Path(args.inbox), ConversationLog(Path(args.out)))
    total = sum(summary.values())
    print(f"backfilled {total} turns across {len(summary)} users: {summary}")


if __name__ == "__main__":
    main()
