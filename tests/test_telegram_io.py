"""telegram_io.sh itself, with `curl` shimmed on PATH: nothing leaves the machine."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
TEXT = '{"ok":true,"result":[{"update_id":42,"message":{"text":"yes please"}}]}'
EMPTY = '{"ok":true,"result":[]}'
ANCHOR = '{"ok":true,"result":[{"update_id":41,"message":{"text":"an old message"}}]}'
SENT = '{"ok":true,"result":{}}'
UNAUTHORIZED = '{"ok":false,"error_code":401,"description":"Unauthorized"}'

pytestmark = pytest.mark.integration


class Shim:
    def __init__(self, tmp_path: Path):
        self.tmp_path = tmp_path
        self.script = tmp_path / "telegram_io.sh"  # a copy: this folder has no .env to read
        self.script.write_text((REPO / "telegram_io.sh").read_text())
        self.script.chmod(0o755)
        (tmp_path / "bin").mkdir()

    def curl(self, *polls: str, anchor: tuple[str, ...] = (ANCHOR,), sends: str = SENT, send_rc: int = 0) -> None:
        """Each entry answers one call in order, the last repeats. `rc:N` fails with exit code N."""
        (self.tmp_path / "polls").write_text("\n".join(polls) + "\n")
        (self.tmp_path / "anchors").write_text("\n".join(anchor) + "\n")
        (self.tmp_path / "bin/curl").write_text(f"""#!/bin/bash
echo "$*" >> {self.tmp_path}/calls
if [[ "$*" == *sendMessage* ]]; then
  [ {send_rc} = 0 ] || exit {send_rc}
  echo '{sends}'; exit 0
fi
if [[ "$*" == *offset=-1* ]]; then queue={self.tmp_path}/anchors; else queue={self.tmp_path}/polls; fi
line=$(head -n 1 "$queue")
[ "$(wc -l < "$queue")" -gt 1 ] && sed -i 1d "$queue"
case "$line" in rc:*) exit "${{line#rc:}}";; esac
echo "$line"
""")
        (self.tmp_path / "bin/curl").chmod(0o755)

    def run(self, *argv: str, timeout: float = 20) -> subprocess.CompletedProcess:
        env = {**os.environ, "PATH": f"{self.tmp_path}/bin:{os.environ['PATH']}", "TELEGRAM_BOT_TOKEN": "t",
               "TELEGRAM_CHAT_ID": "c", "TELEGRAM_POLL_INTERVAL": "0"}
        env.pop("PAINFORWISDOM_STATE_ROOT", None)
        return subprocess.run([str(self.script), *argv], env=env, capture_output=True, text=True, timeout=timeout)

    def calls(self) -> list[str]:
        return (self.tmp_path / "calls").read_text().splitlines()

    def polls(self) -> list[str]:
        return [c for c in self.calls() if "getUpdates" in c and "offset=-1" not in c]


@pytest.fixture
def shim(tmp_path) -> Shim:
    return Shim(tmp_path)


@pytest.mark.parametrize("failure", ["rc:28", "rc:7", "rc:6", "<html>502 Bad Gateway</html>", ""])
def test_a_failed_poll_does_not_end_the_wait(shim, failure):
    shim.curl(failure, failure, TEXT)
    out = shim.run("wait_reply")
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "yes please"
    assert len(shim.polls()) == 3


def test_an_unauthorized_poll_keeps_waiting_as_it_did(shim):
    shim.curl(UNAUTHORIZED, TEXT)
    assert shim.run("wait_reply").stdout.strip() == "yes please"


def test_wait_reply_posts_nothing(shim):
    shim.curl(EMPTY, TEXT)
    assert shim.run("wait_reply").returncode == 0
    assert not any("sendMessage" in c for c in shim.calls())


def test_a_failed_anchor_is_retried_so_an_old_message_is_never_the_answer(shim):
    """Offset 1 means every pending update: a wait that lost its anchor must not start there."""
    shim.curl(TEXT, anchor=("rc:7", "<html>", ANCHOR))
    assert shim.run("wait_reply").stdout.strip() == "yes please"
    assert all("offset=42" in p for p in shim.polls())


def test_no_updates_yet_is_an_anchor(shim):
    shim.curl(TEXT, anchor=(EMPTY,))
    assert shim.run("wait_reply").returncode == 0
    assert all("offset=1" in p for p in shim.polls())


def test_ask_posts_then_waits(shim):
    shim.curl("rc:28", TEXT)
    out = shim.run("ask", "approve?")
    assert out.returncode == 0 and out.stdout.strip() == "yes please"
    assert sum("sendMessage" in c for c in shim.calls()) == 1


def test_ask_says_so_when_the_post_failed(shim):
    shim.curl(TEXT, send_rc=28)
    out = shim.run("ask", "approve?")
    assert out.returncode == 75 and out.stdout == ""
    assert not shim.polls()


def test_send_still_exits_with_curls_code(shim):
    shim.curl(EMPTY, send_rc=28)
    assert shim.run("send", "hello").returncode == 28


def test_send_ok_still_exits_zero(shim):
    shim.curl(EMPTY)
    assert shim.run("send", "hello").returncode == 0


def test_no_token_still_fails_at_once(shim):
    env = {k: v for k, v in os.environ.items() if not k.startswith("TELEGRAM") and k != "PAINFORWISDOM_STATE_ROOT"}
    out = subprocess.run([str(shim.script), "wait_reply"], env=env, capture_output=True, text=True, timeout=10)
    assert out.returncode == 1 and "must be set" in out.stderr
