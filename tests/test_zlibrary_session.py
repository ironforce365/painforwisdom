"""Regression tests for z-library session reuse (2026-06-02 hit-rate incident).

The bridge re-authenticated on every subprocess call; a 60-book batch fired
60-180 logins in ~9 min and tripped the EAPI login rate-limit (HTTP 400),
collapsing yield to 6/60. The fix caches one session and injects it so the
bridge skips /eapi/user/login, with self-heal on a stale key.
"""
from __future__ import annotations

import json
import os
import sys
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline import zlibrary_bridge as zb  # noqa: E402


class SessionEnvTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.sf = Path(tmp.name) / "zlib-session.json"
        p = patch.object(zb, "_SESSION_FILE", self.sf)
        p.start()
        self.addCleanup(p.stop)

    def _write(self, **extra) -> None:
        self.sf.write_text(json.dumps({
            "remix_userid": "2890416",
            "remix_userkey": "k" * 32,
            "domain": "z-library.sk",
            **extra,
        }))

    def test_absent_file_yields_empty(self):
        self.assertEqual(zb._session_env(), {})

    def test_fresh_file_yields_injectable_creds(self):
        self._write()
        env = zb._session_env()
        self.assertEqual(env["ZLIBRARY_REMIX_USERID"], "2890416")
        self.assertEqual(env["ZLIBRARY_REMIX_USERKEY"], "k" * 32)
        self.assertEqual(env["ZLIBRARY_EAPI_DOMAIN"], "z-library.sk")

    def test_expired_file_yields_empty(self):
        self._write()
        old = time.time() - (zb._SESSION_TTL_S + 60)
        os.utime(self.sf, (old, old))
        self.assertEqual(zb._session_env(), {})

    def test_malformed_file_yields_empty(self):
        self.sf.write_text("{not json")
        self.assertEqual(zb._session_env(), {})

    def test_missing_key_yields_empty(self):
        self.sf.write_text(json.dumps({"remix_userid": "x"}))
        self.assertEqual(zb._session_env(), {})


class AuthErrorClassifyTests(unittest.TestCase):
    def test_login_and_401_are_auth_errors(self):
        self.assertTrue(zb._is_auth_error("POST /eapi/user/login 400 Bad Request"))
        self.assertTrue(zb._is_auth_error("HTTP 401 Unauthorized"))
        self.assertTrue(zb._is_auth_error("zlibrary.exception.LoginFailed"))

    def test_unrelated_errors_are_not_auth(self):
        self.assertFalse(zb._is_auth_error("500 Internal Server Error"))
        self.assertFalse(zb._is_auth_error("ReadTimeout"))
        self.assertFalse(zb._is_auth_error(""))


class BridgeCallSelfHealTests(unittest.TestCase):
    """A rejected cached session must be dropped and the call retried once with a
    forced fresh login; a cold call (no session) must NOT auto-retry (that would
    hammer the rate-limited login endpoint)."""

    def setUp(self) -> None:
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.sf = Path(tmp.name) / "zlib-session.json"
        for name in ("ZLIBRARY_PYTHON", "ZLIBRARY_BRIDGE_SCRIPT", "ZLIBRARY_LIB_DIR"):
            # _bridge_call's pre-flight existence checks must pass; point them at
            # a real path (this test file) so it proceeds to _run_bridge.
            pp = patch.object(zb, name, Path(__file__))
            pp.start()
            self.addCleanup(pp.stop)
        ps = patch.object(zb, "_SESSION_FILE", self.sf)
        ps.start()
        self.addCleanup(ps.stop)

    def _valid_session(self) -> None:
        self.sf.write_text(json.dumps({
            "remix_userid": "1", "remix_userkey": "k" * 32, "domain": "z-library.sk",
        }))

    def test_stale_session_cleared_and_retried(self):
        self._valid_session()
        calls = []

        def fake_run(function, args, session_env):
            calls.append(session_env)
            if len(calls) == 1:
                self.assertTrue(session_env, "first call should carry the cached session")
                return 1, "", "auth fail: POST /eapi/user/login rejected"
            return 0, '{"books": []}', ""

        with patch.object(zb, "_run_bridge", side_effect=fake_run):
            rc, out, err = zb._bridge_call("search", {"query": "x"})

        self.assertEqual(rc, 0)
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[1], {}, "retry must drop the session to force fresh login")
        self.assertFalse(self.sf.exists(), "stale session file must be cleared")

    def test_cold_call_does_not_auto_retry(self):
        # No session file → a failure (e.g. rate-limited fresh login) must not
        # trigger an immediate refresh-retry.
        calls = []

        def fake_run(function, args, session_env):
            calls.append(session_env)
            return 1, "", "POST /eapi/user/login 400 Bad Request"

        with patch.object(zb, "_run_bridge", side_effect=fake_run):
            rc, out, err = zb._bridge_call("search", {"query": "x"})

        self.assertEqual(rc, 1)
        self.assertEqual(len(calls), 1, "cold call must not auto-retry")


if __name__ == "__main__":
    unittest.main()
