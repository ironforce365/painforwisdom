"""Persona driver: produces the synthetic user's next message by role-playing a
profile through the Claude CLI (subscription auth, no API key), so it stays in
the project's `claude -p ... --output-format json` convention. The subprocess
runner is injected so the prompt assembly and JSON parsing are tested offline."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from synthetic.driver import build_persona_prompt, persona_reply
from synthetic.profiles import Profile

PROF = Profile(slug="dana", name="Dana", persona="anxious first-time marathoner",
               style="short, lots of questions", opener="am I ready?", turn_count=4,
               goals="finish without walking")

HISTORY = [
    {"role": "user", "content": "am I ready?"},
    {"role": "assistant", "content": "What does your longest run look like so far?"},
]


def test_prompt_includes_persona_style_and_recent_coach_message():
    prompt = build_persona_prompt(PROF, HISTORY)
    assert "anxious first-time marathoner" in prompt
    assert "short, lots of questions" in prompt
    # The coach's last message is in the transcript so the reply is responsive.
    assert "What does your longest run look like" in prompt


def _fake_run(result_text):
    def run(cmd, **kwargs):
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"result": result_text}),
            stderr="",
        )
    return run


def test_persona_reply_returns_cli_result_text():
    reply = persona_reply(PROF, HISTORY, run=_fake_run("Only about 10 miles, is that enough?"))
    assert reply == "Only about 10 miles, is that enough?"


def test_persona_reply_invokes_claude_cli_with_json_and_model():
    captured = {}

    def run(cmd, **kwargs):
        captured["cmd"] = cmd
        return SimpleNamespace(returncode=0, stdout=json.dumps({"result": "ok"}), stderr="")

    persona_reply(PROF, HISTORY, model="sonnet", run=run)
    cmd = captured["cmd"]
    assert cmd[0] == "claude"
    assert "-p" in cmd
    assert "--output-format" in cmd and "json" in cmd
    assert "--model" in cmd and "sonnet" in cmd


def test_persona_reply_raises_on_cli_failure():
    def run(cmd, **kwargs):
        return SimpleNamespace(returncode=1, stdout="", stderr="boom")
    with pytest.raises(RuntimeError):
        persona_reply(PROF, HISTORY, run=run)
