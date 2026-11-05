"""C11 human-in-the-loop mailbox: the first answer from the console or Telegram wins
(spec 2026-11-05-console-end-to-end section 3). Standard library and pipeline.telegram only: the UI server imports it.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from pipeline import telegram as _telegram

POLL_S = 1.0
KILL_WAIT_S = 2.0


def pending_path(run_dir: Path) -> Path:
    return Path(run_dir) / "pending.json"


def reply_path(run_dir: Path) -> Path:
    return Path(run_dir) / "reply.json"


def approval_request(node: str, title: str, prompt: str) -> dict[str, Any]:
    return {"kind": "approval", "node": node, "title": title, "prompt": prompt, "error": None, "non_recoverable": None,
            "options": [{"reply": "yes", "label": "Approve", "style": "primary"},
                        {"reply": "no", "label": "Reject", "style": "danger"}],
            "free_text": True}


def error_request(node: str, error: str, prompt: str, non_recoverable: Optional[str]) -> dict[str, Any]:
    abort = {"reply": "abort", "label": "Abort", "style": "danger"}
    retry = {"reply": "retry", "label": "Retry", "style": "primary"}
    return {"kind": "error", "node": node, "title": f"Error in {node}", "prompt": prompt, "error": error,
            "non_recoverable": non_recoverable, "options": [abort] if non_recoverable else [retry, abort],
            "free_text": False}


def read_pending(run_dir: Path) -> Optional[dict]:
    try:
        pending = json.loads(pending_path(run_dir).read_text())
    except (OSError, ValueError):
        return None
    return pending if isinstance(pending, dict) else None


def clear(run_dir: Path) -> None:
    pending_path(run_dir).unlink(missing_ok=True)
    reply_path(run_dir).unlink(missing_ok=True)


def write_reply(run_dir: Path, pending_id: str, reply: str, source: str = "console") -> bool:
    """Atomic create: complete when visible, and False when a reply is already there (link never overwrites)."""
    path = reply_path(run_dir)
    tmp = path.with_name(f".reply.json.{uuid.uuid4().hex}.tmp")
    tmp.write_text(json.dumps({"id": pending_id, "reply": reply, "source": source, "ts": time.time()}))
    try:
        os.link(tmp, path)
    except FileExistsError:
        return False
    finally:
        tmp.unlink()
    return True


def _take_reply(run_dir: Path, pending_id: str) -> Optional[str]:
    """The reply for this id, if any. Anything else in the mailbox is stale: remove it."""
    path = reply_path(run_dir)
    try:
        reply = json.loads(path.read_text())
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        path.unlink(missing_ok=True)
        return None
    if not isinstance(reply, dict) or reply.get("id") != pending_id:
        print("[hitl] ignoring a reply with a stale id")
        path.unlink(missing_ok=True)
        return None
    return str(reply.get("reply", ""))


def _post(prompt: str) -> Optional[subprocess.Popen]:
    try:
        return _telegram.ask_process(prompt)
    except (OSError, RuntimeError) as exc:  # no script, not executable: the console can still answer
        print(f"[hitl] telegram ask unavailable: {exc}")
        return None


def _telegram_answer(child: subprocess.Popen) -> Optional[str]:
    try:
        out, _ = child.communicate(timeout=KILL_WAIT_S)
    except subprocess.TimeoutExpired:
        _kill(child)
        return None
    return (out or "").strip() if child.returncode == 0 else None


def _kill(child: Optional[subprocess.Popen]) -> None:
    """The whole group: a surviving poll loop would swallow the user's next message."""
    if child is None:
        return
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(child.pid, sig)
        except (ProcessLookupError, PermissionError):
            pass
        try:
            child.communicate(timeout=KILL_WAIT_S)
            return
        except subprocess.TimeoutExpired:
            continue


def _raise_on_sigterm() -> Any:
    """A stopped runner must run the `finally` below. Only the main thread may set a handler."""
    if threading.current_thread() is not threading.main_thread():
        return None

    def _exit(signum: int, _frame: Any) -> None:
        raise SystemExit(128 + signum)

    return signal.signal(signal.SIGTERM, _exit)


def ask(run_dir: Path, request: dict, reminder_interval: float, max_reminders: Optional[int] = None, *,
        telegram: bool = True, poll_s: float = POLL_S) -> Optional[str]:
    """Publish `request`, return the first answer, or None once `max_reminders` posts went unanswered (None: never).

    `telegram=False` waits on the mailbox only (the fake runner).
    """
    run_dir = Path(run_dir)
    pending = {**request, "id": uuid.uuid4().hex, "created_at": time.time()}
    reply_path(run_dir).unlink(missing_ok=True)
    tmp = pending_path(run_dir).with_name(".pending.json.tmp")
    tmp.write_text(json.dumps(pending, indent=2))
    os.replace(tmp, pending_path(run_dir))
    previous = _raise_on_sigterm()
    child = _post(pending["prompt"]) if telegram else None
    try:
        posts = 1
        deadline = time.monotonic() + reminder_interval
        while True:
            answer = _take_reply(run_dir, pending["id"])
            if answer:
                print("[hitl] answered on the console")
                return answer
            if child is not None and child.poll() is not None:
                answer = _telegram_answer(child)
                child = None  # a failed post (no token, no network) waits for the next reminder: no busy loop
                if answer:
                    print("[hitl] answered on telegram")
                    return answer
            left = deadline - time.monotonic()
            if left <= 0:
                _kill(child)
                child = None
                if max_reminders is not None and posts >= max_reminders:
                    print(f"[hitl] no answer after {posts} posts")
                    return None
                print(f"[hitl] no answer after {posts} posts, re-posting")
                posts += 1
                deadline += reminder_interval
                if telegram:
                    child = _post(pending["prompt"])
                continue
            time.sleep(min(poll_s, left))
    finally:
        _kill(child)
        clear(run_dir)
        if previous is not None:
            signal.signal(signal.SIGTERM, previous)
