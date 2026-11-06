"""What a run offers and the actions themselves: reply, stop, retry, restart, dismiss, log
(spec 2026-11-05-console-end-to-end section 3). Never imports pipeline.run or a client that reads secrets.
"""
from __future__ import annotations

import json
import os
import signal
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote

from pipeline import hitl, runtime
from pipeline.publish import package
from pipeline.ui import launcher, publish, runs
from pipeline.ui.settings import Settings

STOP_GRACE_S = 5.0
STOP_KILL_WAIT_S = 2.0
LOG_READ_CAP = 2 * 1024 * 1024
RUNNERS = {b"pipeline.run", b"pipeline.retry", b"pipeline.ui.fake_runner"}
STOPPED = "Stopped from the console."


class ActionError(Exception):
    def __init__(self, status: int, detail: Any, **extra: Any):
        super().__init__(str(detail))
        self.status, self.body = status, {"detail": detail, **extra}


def _run_dir(settings: Settings, run_id: str) -> Path:
    rd = runs.run_dir(settings.processed_root, run_id)
    if rd is None:
        raise ActionError(404, "unknown run")
    return rd


# --- what a run offers -------------------------------------------------------


def _checkpoint_db(profile: str) -> Path:
    """The file the runner of `profile` will open: os.environ, then its env file (load_dotenv never overrides)."""
    path = os.environ.get("CHECKPOINT_DB_PATH")
    if not path:
        from dotenv import dotenv_values  # noqa: PLC0415 - only this key is read; nothing enters this process's env

        path = dotenv_values(runtime.STATE_ROOT / (".env.sandbox" if profile == "sandbox" else ".env")).get("CHECKPOINT_DB_PATH")
    return Path(path) if path else runtime.PROJECT_ROOT / "pipeline" / "checkpoints.db"


def _has_checkpoint(settings: Settings, rec: dict) -> bool:
    if settings.runner == "fake":  # a finished node is what the fake runner resumes after
        return any(ev.get("event") == "end" for ev in rec["events"])
    db = _checkpoint_db(rec["profile"] or settings.profile)
    try:
        with closing(sqlite3.connect(f"file:{quote(str(db))}?mode=ro", uri=True, timeout=1.0)) as conn:
            row = conn.execute("SELECT 1 FROM checkpoints WHERE thread_id = ? LIMIT 1", (rec["run_id"],)).fetchone()
    except sqlite3.Error:
        return False
    return row is not None


def _retry_blocker(settings: Settings, rec: dict) -> Optional[str]:
    if rec["status"] == "done":
        return "this run finished: there is nothing to retry"
    if not _has_checkpoint(settings, rec):
        return "this run has no checkpoint: restart it from scratch"
    return None


def _restart_blocker(rd: Path, rec: dict) -> Optional[str]:
    return None if runs.input_file(rd, rec) else f"the input file is gone: {rec['input'].get('path')}"


def _publish_blocker(rd: Path) -> Optional[str]:
    """A publisher is writing this run's package: a retry's node would race it. A dead one reads failed here."""
    for target in package.NODE_DIRS:
        pkg = publish.reconcile(rd, target)
        if pkg and pkg["status"] == "publishing":
            return f"{target} is publishing: wait for it to finish"
    return None


def available(settings: Settings, rd: Path, run: dict) -> dict:
    """`actions` and `action_blockers` for a load_run result."""
    status, blockers = run["status"], {}
    if status in runs.ACTIVE:
        active = "the run is still active"
        return {"actions": ["reply", "stop"] if run["pending"] else ["stop"],
                "action_blockers": {"retry": active, "restart": active}}
    acts: list[str] = []
    rec = runs._record(rd, None)
    if status in runs.ENDED_NOT_OK:
        busy = runs.active_run_id(settings.processed_root)
        blocked = f"run {busy} is active" if busy else _publish_blocker(rd)
        reasons = {"retry": blocked or _retry_blocker(settings, rec), "restart": blocked or _restart_blocker(rd, rec)}
        acts = [verb for verb, reason in reasons.items() if reason is None]
        blockers = {verb: reason for verb, reason in reasons.items() if reason is not None}
    if runs.needs_decision(rec, rd, None, None):
        acts.append("dismiss")
    return {"actions": acts, "action_blockers": blockers}


# --- reply -------------------------------------------------------------------


def reply(settings: Settings, run_id: str, pending_id: str, text: str) -> None:
    rd = _run_dir(settings, run_id)
    pending = runs.pending_for(rd, runs._record(rd, None)["status"])
    if pending is None or pending.get("id") != pending_id:
        raise ActionError(409, "no such pending request")
    text = text.strip()
    if not text:
        raise ActionError(400, "empty reply")
    if not pending.get("free_text") and text not in {o.get("reply") for o in pending.get("options", [])}:
        raise ActionError(400, "not one of the options")
    # A reply left by an older request would only block this one until the runner's next poll.
    left = hitl.reply_path(rd)
    try:
        if json.loads(left.read_text()).get("id") != pending_id:
            left.unlink(missing_ok=True)
    except (OSError, ValueError):
        pass
    if not hitl.write_reply(rd, pending_id, text):
        raise ActionError(409, "already answered")


# --- stop --------------------------------------------------------------------


def _cmdline(pid: int) -> list[bytes]:
    try:
        return Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
    except OSError:
        return []


def is_runner(pid: Any) -> bool:
    """Pids get reused: only a runner's own cmdline earns a signal."""
    return isinstance(pid, int) and pid > 0 and any(arg in RUNNERS for arg in _cmdline(pid))


def _gone(pid: int) -> bool:
    """Dead or a zombie (empty cmdline): nothing left to wait for."""
    return not any(_cmdline(pid))


def _signal(pid: int, sig: signal.Signals) -> None:
    """The process group a spawned runner leads, so its children go too; a bare pid when it leads none."""
    try:
        if os.getpgid(pid) == pid:
            os.killpg(pid, sig)
        else:
            os.kill(pid, sig)
    except (ProcessLookupError, PermissionError):
        pass


def _wait_gone(pid: int, timeout: float) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if _gone(pid):
            return True
        time.sleep(0.02)
    return _gone(pid)


def stop(settings: Settings, run_id: str) -> None:
    """Returns once the runner is gone and the record says `stopped`, at most STOP_GRACE_S + STOP_KILL_WAIT_S later."""
    rd = _run_dir(settings, run_id)
    rec = runs._record(rd, None)
    if rec["status"] not in runs.ACTIVE:
        raise ActionError(409, "run not active")
    pid = rec["pid"]
    if is_runner(pid):
        _signal(pid, signal.SIGTERM)
        if not _wait_gone(pid, STOP_GRACE_S):
            _signal(pid, signal.SIGKILL)
            _wait_gone(pid, STOP_KILL_WAIT_S)
    hitl.clear(rd)
    if runs._record(rd, None)["status"] != "done":  # it may have finished just before the signal
        runs.update_record(rd, status="stopped", ended_at=time.time(), error=STOPPED)


# --- retry, restart, dismiss -------------------------------------------------


def retry(settings: Settings, run_id: str, auto_approve: bool) -> None:
    rd = _run_dir(settings, run_id)
    with launcher._lock:
        busy = runs.active_run_id(settings.processed_root)
        if busy is not None:
            raise ActionError(409, "run active", run_id=busy)
        rec = runs._record(rd, None)
        blocker = _publish_blocker(rd) or _retry_blocker(settings, rec)
        if blocker:
            raise ActionError(409, blocker)
        hitl.clear(rd)
        # This server's pid until the child exists: the run reads active at once and the lock's next holder sees it.
        runs.update_record(rd, status="running", verdict=None, pid=os.getpid(), ended_at=None, error=None, dismissed=False)
        try:
            pid = launcher.spawn(launcher.build_retry_argv(settings, run_id, rec["profile"], auto_approve), rd / "stdout.log")
        except Exception as exc:
            runs.update_record(rd, status="failed", ended_at=time.time(), error=f"spawn failed: {exc}")
            raise
        runs.update_record(rd, pid=pid)


def restart(settings: Settings, run_id: str, auto_approve: bool) -> str:
    rd = _run_dir(settings, run_id)
    busy = runs.active_run_id(settings.processed_root)
    if busy is not None:
        raise ActionError(409, "run active", run_id=busy)
    publishing = _publish_blocker(rd)
    if publishing:
        raise ActionError(409, publishing)
    rec = runs._record(rd, None)
    blocker = _restart_blocker(rd, rec)
    if blocker:
        raise ActionError(404, blocker)
    path = runs.input_file(rd, rec)
    kind = launcher.input_kind(path.name) or rec["input"].get("kind") or "transcript"
    try:
        return launcher.launch_path(settings, path, kind, auto_approve, None, rec["language"])
    except launcher.LaunchError as e:
        raise ActionError(e.status, e.detail, run_id=e.run_id) from e


def dismiss(settings: Settings, run_id: str, dismissed: bool) -> None:
    runs.update_record(_run_dir(settings, run_id), dismissed=dismissed)


# --- log ---------------------------------------------------------------------


def log_tail(settings: Settings, run_id: str, tail: int) -> dict:
    path = _run_dir(settings, run_id) / "stdout.log"
    try:
        size = path.stat().st_size
        with open(path, "rb") as f:
            f.seek(max(0, size - LOG_READ_CAP))
            raw = f.read(LOG_READ_CAP)
    except FileNotFoundError:
        return {"lines": [], "bytes": 0}
    lines = raw.decode("utf-8", errors="replace").splitlines()
    if size > LOG_READ_CAP:
        lines = lines[1:]  # the read began mid-line
    return {"lines": lines[-tail:], "bytes": size}
