"""Print a v2 run's audit trail: `python -m pipeline.v2.audit <run_id> [--session <id>] [--db <path>]` (spec §3)."""
from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime
from typing import List, Optional

from pipeline.v2.store import default_path


def _ts(t: Optional[float]) -> str:
    return datetime.fromtimestamp(t).strftime("%Y-%m-%d %H:%M:%S") if t else "-"


def _dur(a: Optional[float], b: Optional[float]) -> str:
    return f"{b - a:.1f}s" if a and b else "-"


def trail(conn: sqlite3.Connection, run_id: str) -> List[str]:
    run = conn.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
    if run is None:
        return [f"no run {run_id}"]
    out = [f"run {run_id}  engine={run['engine']} profile={run['profile']} dry_run={bool(run['dry_run'])}"
           f" status={run['status']} verdict={run['verdict']}",
           f"  input {run['input_kind']} {run['input_path']} sha256={run['input_sha256']} language={run['language']}"
           f" git={run['git_sha']}",
           f"  {_ts(run['started_at'])} to {_ts(run['ended_at'])} ({_dur(run['started_at'], run['ended_at'])})"]
    if run["error"]:
        out.append(f"  error: {run['error']}")

    out.append("\nsteps")
    for s in conn.execute("SELECT * FROM steps WHERE run_id=? ORDER BY started_at, id", (run_id,)):
        out.append(f"  #{s['id']} {s['node']} attempt {s['attempt']}  {s['status']}"
                   f"  {_dur(s['started_at'], s['ended_at'])}" + (f"  error: {s['error']}" if s["error"] else ""))

    out.append("\nsessions")
    for s in conn.execute("SELECT s.*, st.node FROM sessions s LEFT JOIN steps st ON st.id = s.step_id"
                          " WHERE s.run_id=? ORDER BY s.id", (run_id,)):
        out.append(f"  #{s['id']} {s['node'] or '-'} {s['model']}  in={s['input_tokens']} out={s['output_tokens']}"
                   f" cache_read={s['cache_read_tokens']} cache_write={s['cache_creation_tokens']}"
                   f" turns={s['num_turns']} stop={s['stop_reason']}"
                   + (f"  error: {s['error']}" if s["error"] else ""))

    out.append("\napprovals")
    for h in conn.execute("SELECT * FROM hitl WHERE run_id=? ORDER BY asked_at, id", (run_id,)):
        out.append(f"  #{h['id']} step {h['step_id']} {h['kind']}  asked {_ts(h['asked_at'])}"
                   f"  reply={h['reply']!r} {_ts(h['answered_at'])}")

    out.append("\nartifacts")
    for a in conn.execute("SELECT * FROM artifacts WHERE run_id=? ORDER BY id", (run_id,)):
        out.append(f"  step {a['step_id']} {a['path']}  sha256={a['sha256'] or 'missing'} bytes={a['bytes']}")
    return out


def session_detail(conn: sqlite3.Connection, session_row_id: int) -> List[str]:
    s = conn.execute("SELECT * FROM sessions WHERE id=?", (session_row_id,)).fetchone()
    if s is None:
        return [f"no session {session_row_id}"]
    return [f"session #{s['id']} run={s['run_id']} sdk_session={s['session_id']} model={s['model']}",
            f"allowed_tools={s['allowed_tools']} max_turns={s['max_turns']} max_tokens={s['max_tokens']}",
            f"\n--- system prompt (sha256 {s['system_prompt_sha256']}) ---", s["system_prompt"] or "",
            f"\n--- user message (sha256 {s['user_message_sha256']}) ---", s["user_message"] or "",
            "\n--- text ---", s["text"] or ""]


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(prog="python -m pipeline.v2.audit")
    p.add_argument("run_id", nargs="?")
    p.add_argument("--session", type=int, help="sessions.id row to print in full")
    p.add_argument("--db", help="store path (default: PIPELINE_V2_DB or pipeline/v2.db)")
    args = p.parse_args(argv)
    if args.run_id is None and args.session is None:
        p.error("give a run_id or --session")
    path = args.db or default_path()
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        print(f"cannot open {path}: {exc}", file=sys.stderr)
        return 1
    conn.row_factory = sqlite3.Row
    lines = session_detail(conn, args.session) if args.session is not None else trail(conn, args.run_id)
    print("\n".join(lines))
    return 1 if lines[0].startswith("no ") else 0


if __name__ == "__main__":
    sys.exit(main())
