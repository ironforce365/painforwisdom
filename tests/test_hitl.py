"""C11 mailbox racing Telegram (spec 2026-11-05-console-end-to-end section 3). The Telegram script is always a stub."""
from __future__ import annotations

import json
import os
import signal
import stat
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from pipeline import hitl, telegram

REPO = Path(__file__).resolve().parent.parent
REQ = hitl.approval_request("kb_curator", "New theme: approval needed", "approve?")


def _stub(tmp_path: Path, monkeypatch, body: str) -> Path:
    script = tmp_path / "telegram_io.sh"
    script.write_text(f"#!/bin/bash\necho \"$$\" >> {tmp_path}/pids\necho \"$2\" >> {tmp_path}/posts\n{body}\n")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setattr(telegram, "TELEGRAM_IO", script)
    return script


def _wait_for(path: Path, timeout: float = 5.0) -> None:
    end = time.monotonic() + timeout
    while not path.exists():
        assert time.monotonic() < end, f"{path} never appeared"
        time.sleep(0.01)


def _ask_async(rd: Path, **kw) -> dict:
    out: dict = {}
    kw.setdefault("reminder_interval", 30)
    kw.setdefault("poll_s", 0.02)
    t = threading.Thread(target=lambda: out.setdefault("reply", hitl.ask(rd, kw.pop("request", REQ), **kw)))
    t.start()
    out["thread"] = t
    return out


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    try:
        return Path(f"/proc/{pid}/stat").read_text().split()[2] != "Z"
    except OSError:
        return False


def _read_pid(tmp_path: Path) -> int:
    _wait_for(tmp_path / "pids")
    return int((tmp_path / "pids").read_text().split()[0])


def test_request_builders_match_c11():  # [DT-A1] [DT-E1] [DT-E4]
    assert REQ["kind"] == "approval" and REQ["free_text"] is True
    assert [o["reply"] for o in REQ["options"]] == ["yes", "no"]
    ok = hitl.error_request("research", "RuntimeError: x", "p", None)
    assert ok["kind"] == "error" and ok["free_text"] is False and ok["non_recoverable"] is None
    assert [o["reply"] for o in ok["options"]] == ["retry", "abort"]
    dead = hitl.error_request("research", "RuntimeError: x", "p", "reason")
    assert [o["reply"] for o in dead["options"]] == ["abort"] and dead["non_recoverable"] == "reason"


def test_console_reply_wins_and_kills_the_telegram_child(tmp_path, monkeypatch):  # [DT-A1]
    _stub(tmp_path, monkeypatch, "sleep 60 & wait")
    out = _ask_async(tmp_path)
    _wait_for(tmp_path / "pending.json")
    pending = json.loads((tmp_path / "pending.json").read_text())
    assert pending["kind"] == "approval" and pending["id"] and pending["created_at"] > 0
    pid = _read_pid(tmp_path)
    assert _alive(pid)
    assert hitl.write_reply(tmp_path, pending["id"], "yes")
    out["thread"].join(5)
    assert out["reply"] == "yes"
    # Group killed: neither the script nor the sleep it started can swallow the user's next message.
    end = time.monotonic() + 3
    while _alive(pid) and time.monotonic() < end:
        time.sleep(0.01)
    assert not _alive(pid)
    assert not (tmp_path / "pending.json").exists() and not (tmp_path / "reply.json").exists()


def test_telegram_answer_returns_and_cleans_up(tmp_path, monkeypatch):  # [DT-A4]
    _stub(tmp_path, monkeypatch, "sleep 0.1; echo ' retry '")
    out = _ask_async(tmp_path)
    out["thread"].join(5)
    assert out["reply"] == "retry"
    assert not (tmp_path / "pending.json").exists()


def test_reply_with_another_id_is_ignored_and_removed(tmp_path, monkeypatch):  # [DT-A1]
    _stub(tmp_path, monkeypatch, "sleep 60 & wait")
    out = _ask_async(tmp_path)
    _wait_for(tmp_path / "pending.json")
    assert hitl.write_reply(tmp_path, "stale", "yes")
    end = time.monotonic() + 3
    while (tmp_path / "reply.json").exists() and time.monotonic() < end:
        time.sleep(0.01)
    assert not (tmp_path / "reply.json").exists()
    assert out["thread"].is_alive()
    pending = json.loads((tmp_path / "pending.json").read_text())
    assert hitl.write_reply(tmp_path, pending["id"], "no")
    out["thread"].join(5)
    assert out["reply"] == "no"


def test_second_concurrent_reply_cannot_land(tmp_path):  # [DT-A1]
    assert hitl.write_reply(tmp_path, "i", "yes") is True
    assert hitl.write_reply(tmp_path, "i", "no") is False
    assert json.loads((tmp_path / "reply.json").read_text())["reply"] == "yes"


def test_budget_exhausted_returns_none_after_reposts(tmp_path, monkeypatch):  # [DT-E5]
    _stub(tmp_path, monkeypatch, "sleep 60 & wait")
    assert hitl.ask(tmp_path, REQ, reminder_interval=0.1, max_reminders=2, poll_s=0.02) is None
    assert len((tmp_path / "posts").read_text().splitlines()) == 2
    assert not (tmp_path / "pending.json").exists()
    for pid in map(int, (tmp_path / "pids").read_text().split()):
        end = time.monotonic() + 3
        while _alive(pid) and time.monotonic() < end:
            time.sleep(0.01)
        assert not _alive(pid)


def test_telegram_failure_does_not_stop_the_console_wait(tmp_path, monkeypatch):  # [DT-A1] [DT-E1]
    _stub(tmp_path, monkeypatch, "exit 1")
    out = _ask_async(tmp_path, reminder_interval=0.05)
    _wait_for(tmp_path / "pending.json")
    time.sleep(0.3)  # several intervals of instant failures: still waiting, re-posting once per interval, no busy loop
    assert out["thread"].is_alive()
    assert len((tmp_path / "posts").read_text().splitlines()) <= 8
    assert hitl.write_reply(tmp_path, json.loads((tmp_path / "pending.json").read_text())["id"], "retry")
    out["thread"].join(5)
    assert out["reply"] == "retry"


def _modes(tmp_path: Path) -> list[str]:
    log = tmp_path / "modes"
    return log.read_text().split() if log.exists() else []


def _modal(tmp_path: Path, monkeypatch, body: str, backoff: float = 0.05, backoff_max: float = 0.05) -> None:
    """A stub that logs how it was called."""
    _stub(tmp_path, monkeypatch, f"echo \"$1\" >> {tmp_path}/modes\n{body}")
    monkeypatch.setattr(hitl, "RESPAWN_BACKOFF_S", backoff)
    monkeypatch.setattr(hitl, "RESPAWN_BACKOFF_MAX_S", backoff_max)


def test_a_dead_poller_comes_back_without_posting_again_and_hears_the_reply(tmp_path, monkeypatch):  # [DT-A4]
    _modal(tmp_path, monkeypatch, 'if [ "$1" = ask ]; then exit 28; fi\necho " yes "')
    assert hitl.ask(tmp_path, REQ, reminder_interval=30, poll_s=0.01) == "yes"
    assert _modes(tmp_path) == ["ask", "wait_reply"]


def test_a_failed_post_is_posted_again_after_the_backoff(tmp_path, monkeypatch):  # [DT-A4]
    """Exit 75: the prompt never went out, so a wait alone would leave the user unaware until the next reminder."""
    _modal(tmp_path, monkeypatch, f'n=$(wc -l < {tmp_path}/modes)\nif [ "$n" -lt 3 ]; then exit 75; fi\necho yes')
    assert hitl.ask(tmp_path, REQ, reminder_interval=30, poll_s=0.01) == "yes"
    assert _modes(tmp_path) == ["ask", "ask", "ask"]


def test_after_a_failed_post_succeeds_a_death_only_waits(tmp_path, monkeypatch):  # [DT-A4]
    _modal(tmp_path, monkeypatch, f'n=$(wc -l < {tmp_path}/modes)\nif [ "$n" = 1 ]; then exit 75; fi\n'
                                  f'if [ "$n" = 2 ]; then exit 28; fi\necho yes')
    assert hitl.ask(tmp_path, REQ, reminder_interval=30, poll_s=0.01) == "yes"
    assert _modes(tmp_path) == ["ask", "ask", "wait_reply"]


def test_a_child_that_keeps_dying_backs_off(tmp_path, monkeypatch):  # [DT-A1]
    """No token: every spawn dies at once. The wait neither spins nor stops answering the console."""
    _modal(tmp_path, monkeypatch, "exit 1", backoff=0.1, backoff_max=0.4)
    out = _ask_async(tmp_path, reminder_interval=30)
    _wait_for(tmp_path / "pending.json")
    time.sleep(1.2)  # 0.1 + 0.2 + 0.4 + 0.4 of backoff fit: 5 spawns at most, and 1200 at the poll rate
    spawns = len(_modes(tmp_path))
    assert 3 <= spawns <= 6, spawns
    assert out["thread"].is_alive()
    assert hitl.write_reply(tmp_path, json.loads((tmp_path / "pending.json").read_text())["id"], "yes")
    out["thread"].join(5)
    assert out["reply"] == "yes"


def test_the_reminder_cadence_survives_a_dying_child(tmp_path, monkeypatch):  # [DT-E5]
    _modal(tmp_path, monkeypatch, "exit 28")
    assert hitl.ask(tmp_path, REQ, reminder_interval=0.3, max_reminders=3, poll_s=0.01) is None
    assert len([m for m in _modes(tmp_path) if m == "ask"]) == 3, "one post per reminder, none from the respawns"
    assert "wait_reply" in _modes(tmp_path)


def test_a_reminder_kills_the_respawned_poller(tmp_path, monkeypatch):  # [DT-E5]
    _modal(tmp_path, monkeypatch, 'if [ "$1" = ask ]; then exit 28; fi\nsleep 60 & wait')
    assert hitl.ask(tmp_path, REQ, reminder_interval=0.3, max_reminders=2, poll_s=0.01) is None
    for pid in map(int, (tmp_path / "pids").read_text().split()):
        end = time.monotonic() + 3
        while _alive(pid) and time.monotonic() < end:
            time.sleep(0.01)
        assert not _alive(pid)


def test_console_reply_wins_over_a_respawned_poller(tmp_path, monkeypatch):  # [DT-A1]
    _modal(tmp_path, monkeypatch, 'if [ "$1" = ask ]; then exit 28; fi\nsleep 60 & wait')
    out = _ask_async(tmp_path)
    end = time.monotonic() + 5
    while "wait_reply" not in _modes(tmp_path):
        assert time.monotonic() < end
        time.sleep(0.01)
    assert hitl.write_reply(tmp_path, json.loads((tmp_path / "pending.json").read_text())["id"], "no")
    out["thread"].join(5)
    assert out["reply"] == "no"


def test_wait_process_runs_wait_reply_in_its_own_group(tmp_path, monkeypatch):  # [DT-A4]
    _stub(tmp_path, monkeypatch, 'echo "$1"')
    proc = telegram.wait_process()
    out, _ = proc.communicate(timeout=5)
    assert out.strip() == "wait_reply" and proc.pid != os.getpgrp()


def test_missing_telegram_script_does_not_stop_the_console_wait(tmp_path, monkeypatch):  # [DT-A1]
    monkeypatch.setattr(telegram, "TELEGRAM_IO", tmp_path / "nope.sh")
    out = _ask_async(tmp_path)
    _wait_for(tmp_path / "pending.json")
    assert hitl.write_reply(tmp_path, json.loads((tmp_path / "pending.json").read_text())["id"], "yes")
    out["thread"].join(5)
    assert out["reply"] == "yes"


def test_telegram_false_never_spawns(tmp_path, monkeypatch):  # [DT-A1]
    monkeypatch.setattr(telegram, "ask_process", lambda prompt: pytest.fail("must not post"))
    assert hitl.ask(tmp_path, REQ, reminder_interval=0.05, max_reminders=1, telegram=False, poll_s=0.01) is None


def test_ask_process_prefixes_and_is_its_own_group(tmp_path, monkeypatch):  # [DT-A1]
    _stub(tmp_path, monkeypatch, "echo done")
    monkeypatch.setenv("TELEGRAM_MESSAGE_PREFIX", "[SANDBOX] ")
    proc = telegram.ask_process("hello")
    out, _ = proc.communicate(timeout=5)
    assert out.strip() == "done"
    assert (tmp_path / "posts").read_text().strip() == "[SANDBOX] hello"
    assert proc.pid != os.getpgrp()


@pytest.mark.integration
def test_sigterm_during_a_wait_kills_the_telegram_child(tmp_path):  # [DT-R3]
    script = tmp_path / "telegram_io.sh"
    script.write_text(f"#!/bin/bash\necho \"$$\" >> {tmp_path}/pids\nsleep 60 & wait\n")
    script.chmod(0o755)
    code = (
        "import sys; from pathlib import Path; from pipeline import hitl, telegram;"
        f"telegram.TELEGRAM_IO = Path({str(script)!r});"
        f"hitl.ask(Path({str(tmp_path)!r}), hitl.approval_request('n', 't', 'p'), 60, poll_s=0.02)"
    )
    proc = subprocess.Popen([sys.executable, "-c", code], cwd=REPO)
    try:
        pid = _read_pid(tmp_path)
        _wait_for(tmp_path / "pending.json")
        proc.send_signal(signal.SIGTERM)
        proc.wait(10)
        end = time.monotonic() + 3
        while _alive(pid) and time.monotonic() < end:
            time.sleep(0.01)
        assert not _alive(pid)
        assert not (tmp_path / "pending.json").exists()
    finally:
        proc.kill()
