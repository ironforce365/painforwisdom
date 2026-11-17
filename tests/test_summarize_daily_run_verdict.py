"""Daily brief run verdict: signature dedup across systemd retries and zero-brief streak (io-a3179469)."""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.summarize_daily import __main__ as m  # noqa: E402

TODAY = date(2026, 11, 17)
CRASHES = [
    "FileNotFoundError: [Errno 2] No such file or directory: '/x/briefs/.cache/daily-589b501eb7e78e3f02b7430b.txt'",
    "FileNotFoundError: [Errno 2] No such file or directory: '/x/briefs/.cache/daily-c512e711eda221e40161e3a8.txt'",
    "RuntimeError: rate limited",
]


def test_signatures_collapse_hash_only_differences():
    assert len(m._distinct_signatures(CRASHES)) == 2


def test_streak_counts_days_since_last_logged_brief(tmp_path):
    log = tmp_path / "runs.jsonl"
    log.write_text(
        json.dumps({"ts": "2026-11-01T06:00:00"}) + "\n"
        + json.dumps({"ts": "2026-11-02T06:10:00"}) + "\nnot json\n"
    )
    assert m._zero_brief_streak(TODAY, log) == (15, "2026-11-02")
    assert m._zero_brief_streak(TODAY, tmp_path / "missing.jsonl") == (None, "")


def test_retry_same_day_same_signatures_sends_once(tmp_path):
    sent = []
    state = tmp_path / "state.json"
    log = tmp_path / "runs.jsonl"
    log.write_text(json.dumps({"ts": "2026-11-02T06:10:00"}) + "\n")
    with patch.object(m, "ALERT_STATE", state), patch.object(m, "DAILY_LOG", log), \
            patch("pipeline.telegram.send", side_effect=lambda text, **_: sent.append(text) or 0):
        for _ in range(3):  # first run + 2 systemd retries
            m._send_run_verdict_telegram(0, 3, CRASHES, today=TODAY)
        assert len(sent) == 1
        assert "0/3 produced" in sent[0] and "0 briefs for 15 day(s)" in sent[0]
        assert sent[0].count("FileNotFoundError") == 1

        m._send_run_verdict_telegram(0, 3, ["KeyError: 'x'"], today=TODAY)  # new signature
        m._send_run_verdict_telegram(0, 3, CRASHES, today=date(2026, 11, 18))  # next day
        assert len(sent) == 3
