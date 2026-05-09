"""Wraps telegram_io.sh for use from Python nodes.

Two operations exposed:
  - send(text): fire-and-forget notification
  - ask(prompt, timeout): post a question and wait for the next reply

Both shell out to ./telegram_io.sh; we don't reimplement Telegram API calls.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Optional


PROJECT_ROOT = Path(__file__).resolve().parent.parent
TELEGRAM_IO = PROJECT_ROOT / "telegram_io.sh"


def _check() -> None:
    if not TELEGRAM_IO.is_file():
        raise RuntimeError(f"telegram_io.sh not found at {TELEGRAM_IO}")


def _prefix(text: str) -> str:
    """Prepend `TELEGRAM_MESSAGE_PREFIX` env var if set. Used so sandbox runs
    tag every message with `[SANDBOX] ` and the user can ignore them at a
    glance."""
    prefix = os.environ.get("TELEGRAM_MESSAGE_PREFIX", "")
    return f"{prefix}{text}" if prefix else text


def send(text: str) -> int:
    """Fire-and-forget. Returns subprocess exit code."""
    _check()
    proc = subprocess.run(
        [str(TELEGRAM_IO), "send", _prefix(text)],
        cwd=str(PROJECT_ROOT),
        env=os.environ.copy(),
        check=False,
        capture_output=True,
        text=True,
    )
    return proc.returncode


def ask(prompt: str, timeout_seconds: int = 1800) -> Optional[str]:
    """Post `prompt` and wait up to `timeout_seconds` for the user's next reply.

    The underlying telegram_io.sh `ask` polls until a reply lands; we enforce
    the wall-clock timeout from Python via subprocess.timeout so the pipeline
    doesn't hang forever on an offline user.

    Returns the reply text, or None on timeout / subprocess failure.
    """
    _check()
    try:
        proc = subprocess.run(
            [str(TELEGRAM_IO), "ask", _prefix(prompt)],
            cwd=str(PROJECT_ROOT),
            env=os.environ.copy(),
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        return None
    if proc.returncode != 0:
        return None
    reply = (proc.stdout or "").strip()
    return reply or None
