"""SQLite audit store for pipeline v2 (spec 2026-11-09-session-pipeline-v2 §3). Standard library only."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

log = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY, engine TEXT, profile TEXT, dry_run INTEGER, input_kind TEXT, input_path TEXT,
    input_sha256 TEXT, language TEXT, git_sha TEXT, started_at REAL, ended_at REAL, status TEXT, verdict TEXT,
    error TEXT);
CREATE TABLE IF NOT EXISTS steps (
    id INTEGER PRIMARY KEY, run_id TEXT, node TEXT, attempt INTEGER, started_at REAL, ended_at REAL,
    status TEXT, error TEXT, state_in_keys TEXT, state_out TEXT);
CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY, run_id TEXT, step_id INTEGER, session_id TEXT, model TEXT, system_prompt TEXT,
    system_prompt_sha256 TEXT, user_message TEXT, user_message_sha256 TEXT, allowed_tools TEXT, max_turns INTEGER,
    max_tokens INTEGER, text TEXT, messages TEXT, input_tokens INTEGER, output_tokens INTEGER,
    cache_read_tokens INTEGER, cache_creation_tokens INTEGER, notional_cost_usd REAL, duration_s REAL,
    num_turns INTEGER, stop_reason TEXT, sdk_version TEXT, cli_version TEXT, error TEXT);
CREATE TABLE IF NOT EXISTS hitl (
    id INTEGER PRIMARY KEY, run_id TEXT, step_id INTEGER, kind TEXT, request TEXT, reply TEXT, asked_at REAL,
    answered_at REAL);
CREATE TABLE IF NOT EXISTS artifacts (
    id INTEGER PRIMARY KEY, run_id TEXT, step_id INTEGER, path TEXT, sha256 TEXT, bytes INTEGER);
CREATE INDEX IF NOT EXISTS steps_run ON steps(run_id);
CREATE INDEX IF NOT EXISTS sessions_run ON sessions(run_id);
CREATE INDEX IF NOT EXISTS hitl_run ON hitl(run_id);
CREATE INDEX IF NOT EXISTS artifacts_run ON artifacts(run_id);
"""


def default_path() -> Path:
    # Read at call time, not import: profile env and dry_run.activate may set it after this module loads.
    return Path(os.environ.get("PIPELINE_V2_DB") or (PROJECT_ROOT / "pipeline" / "v2.db"))


def sha256_text(text: Optional[str]) -> Optional[str]:
    return None if text is None else hashlib.sha256(text.encode("utf-8")).hexdigest()


def _json(value: Any) -> Optional[str]:
    return None if value is None else json.dumps(value, default=str)


def _file_hash(path: str) -> tuple[Optional[str], Optional[int]]:
    try:
        digest = hashlib.sha256()
        size = 0
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                digest.update(chunk)
                size += len(chunk)
        return digest.hexdigest(), size
    except OSError:
        return None, None


class Store:
    """One connection per thread, WAL, each write its own transaction. Write errors are logged, never raised."""

    def __init__(self, path: Optional[os.PathLike | str] = None):
        self.path = Path(path) if path is not None else default_path()
        self._local = threading.local()
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            conn = self._conn()
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(SCHEMA)
        except (sqlite3.Error, OSError):
            log.exception("v2 audit store: cannot open %s", self.path)

    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.path, timeout=30, isolation_level=None)
            self._local.conn = conn
        return conn

    def _write(self, sql: str, params: Iterable[Any] = (), many: bool = False) -> Optional[int]:
        try:
            conn = self._conn()
            with conn:  # isolation_level=None: explicit BEGIN so a batch is one transaction
                conn.execute("BEGIN")
                cur = conn.executemany(sql, params) if many else conn.execute(sql, tuple(params))
                return cur.lastrowid
        except (sqlite3.Error, OSError):
            log.exception("v2 audit store: write failed")  # audit must never fail a run (spec §3)
            return None

    def start_run(self, run_id: str, *, engine: str = "sessions", profile: Optional[str] = None,
                  dry_run: bool = False, input_kind: Optional[str] = None, input_path: Optional[str] = None,
                  input_sha256: Optional[str] = None, language: Optional[str] = None,
                  git_sha: Optional[str] = None, started_at: Optional[float] = None) -> None:
        self._write(
            "INSERT INTO runs (run_id, engine, profile, dry_run, input_kind, input_path, input_sha256, language,"
            " git_sha, started_at, status) VALUES (?,?,?,?,?,?,?,?,?,?, 'running')"
            " ON CONFLICT(run_id) DO UPDATE SET engine=excluded.engine, profile=excluded.profile,"
            " dry_run=excluded.dry_run, input_kind=excluded.input_kind, input_path=excluded.input_path,"
            " input_sha256=excluded.input_sha256, language=excluded.language, git_sha=excluded.git_sha,"
            " started_at=excluded.started_at, ended_at=NULL, status='running', verdict=NULL, error=NULL",
            (run_id, engine, profile, int(bool(dry_run)), input_kind, input_path, input_sha256, language, git_sha,
             started_at if started_at is not None else time.time()))

    def end_run(self, run_id: str, status: str, *, verdict: Optional[str] = None, error: Optional[str] = None,
                ended_at: Optional[float] = None) -> None:
        self._write("UPDATE runs SET ended_at=?, status=?, verdict=?, error=? WHERE run_id=?",
                    (ended_at if ended_at is not None else time.time(), status, verdict, error, run_id))

    def start_step(self, run_id: str, node: str, attempt: int, state_in_keys: Iterable[str] = ()) -> Optional[int]:
        return self._write(
            "INSERT INTO steps (run_id, node, attempt, started_at, status, state_in_keys) VALUES (?,?,?,?,?,?)",
            (run_id, node, attempt, time.time(), "running", _json(sorted(state_in_keys))))

    def end_step(self, step_id: Optional[int], status: str, *, error: Optional[str] = None,
                 state_out: Optional[Dict[str, Any]] = None) -> None:
        """`state_out` arrives already capped by the caller (`pipeline.graph.end_result`), per Q3904."""
        if step_id is None:
            return
        self._write("UPDATE steps SET ended_at=?, status=?, error=?, state_out=? WHERE id=?",
                    (time.time(), status, error, _json(state_out), step_id))

    def record_session(self, run_id: str, step_id: Optional[int], *, system_prompt: str, user_message: str,
                       model: Optional[str] = None, session_id: Optional[str] = None,
                       allowed_tools: Iterable[str] = (), max_turns: Optional[int] = None,
                       max_tokens: Optional[int] = None, text: Optional[str] = None, messages: Any = None,
                       input_tokens: Optional[int] = None, output_tokens: Optional[int] = None,
                       cache_read_tokens: Optional[int] = None, cache_creation_tokens: Optional[int] = None,
                       notional_cost_usd: Optional[float] = None, duration_s: Optional[float] = None,
                       num_turns: Optional[int] = None, stop_reason: Optional[str] = None,
                       sdk_version: Optional[str] = None, cli_version: Optional[str] = None,
                       error: Optional[str] = None) -> Optional[int]:
        return self._write(
            "INSERT INTO sessions (run_id, step_id, session_id, model, system_prompt, system_prompt_sha256,"
            " user_message, user_message_sha256, allowed_tools, max_turns, max_tokens, text, messages, input_tokens,"
            " output_tokens, cache_read_tokens, cache_creation_tokens, notional_cost_usd, duration_s, num_turns,"
            " stop_reason, sdk_version, cli_version, error) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (run_id, step_id, session_id, model, system_prompt, sha256_text(system_prompt), user_message,
             sha256_text(user_message), _json(list(allowed_tools)), max_turns, max_tokens, text, _json(messages),
             input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens, notional_cost_usd, duration_s,
             num_turns, stop_reason, sdk_version, cli_version, error))

    def record_hitl_ask(self, run_id: str, step_id: Optional[int], kind: str, request: Any) -> Optional[int]:
        return self._write("INSERT INTO hitl (run_id, step_id, kind, request, asked_at) VALUES (?,?,?,?,?)",
                           (run_id, step_id, kind, _json(request), time.time()))

    def record_hitl_reply(self, hitl_id: Optional[int], reply: str) -> None:
        if hitl_id is None:
            return
        self._write("UPDATE hitl SET reply=?, answered_at=? WHERE id=?", (reply, time.time(), hitl_id))

    def record_artifacts(self, run_id: str, step_id: Optional[int], out: Optional[Dict[str, Any]]) -> None:
        """Every non-empty `*_path` string in a step's return dict, hashed if the file exists."""
        rows = [(run_id, step_id, v, *_file_hash(v)) for k, v in (out or {}).items()
                if k.endswith("_path") and isinstance(v, str) and v]
        if rows:
            self._write("INSERT INTO artifacts (run_id, step_id, path, sha256, bytes) VALUES (?,?,?,?,?)",
                        rows, many=True)
