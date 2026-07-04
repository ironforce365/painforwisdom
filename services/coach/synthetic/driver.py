"""Persona driver — produces the synthetic user's next message.

It role-plays a `Profile` through the Claude CLI (`claude -p ... --output-format
json`), which carries the Max subscription auth — no ANTHROPIC_API_KEY burn, per
the project convention for scripted LLM calls. The subprocess runner is injected
(`run`) so prompt assembly + JSON parsing are unit-tested without a real CLI.
"""
from __future__ import annotations

import json
import subprocess
from typing import Callable

from synthetic.profiles import Profile

# A driver turn is a single short message — generous but bounded so a wedged CLI
# call can't hang a long multi-turn run forever.
_CLI_TIMEOUT_S = 120


def build_persona_prompt(profile: Profile, history: list[dict]) -> str:
    """Assemble the CLI prompt: who the runner is, plus the conversation so far,
    asking only for their next message (not narration)."""
    transcript_lines = []
    for turn in history:
        who = "COACH" if turn["role"] == "assistant" else "YOU"
        transcript_lines.append(f"{who}: {turn['content']}")
    transcript = "\n".join(transcript_lines) if transcript_lines else "(no messages yet)"
    goals = f"\nYOUR GOAL: {profile.goals}" if profile.goals else ""
    style = f"\nSTYLE: {profile.style}" if profile.style else ""
    return (
        "You are role-playing a person talking to a running/endurance coach. Stay "
        "fully in character as this person — you are the USER, not the coach.\n\n"
        f"WHO YOU ARE ({profile.name}):\n{profile.persona}{style}{goals}\n\n"
        "CONVERSATION SO FAR:\n"
        f"{transcript}\n\n"
        "Write ONLY your next message to the coach — one to three sentences, in "
        "character. No quotation marks, no narration, no stage directions."
    )


def persona_reply(
    profile: Profile,
    history: list[dict],
    *,
    model: str = "sonnet",
    run: Callable = subprocess.run,
) -> str:
    """Generate the runner's next message via the Claude CLI. Raises RuntimeError
    on a non-zero exit or unparseable output (the harness decides whether to
    retry or substitute a fallback)."""
    prompt = build_persona_prompt(profile, history)
    proc = run(
        ["claude", "-p", prompt, "--output-format", "json", "--model", model],
        capture_output=True,
        text=True,
        timeout=_CLI_TIMEOUT_S,
    )
    if getattr(proc, "returncode", 1) != 0:
        raise RuntimeError(f"claude CLI failed ({proc.returncode}): {proc.stderr}")
    try:
        result = json.loads(proc.stdout).get("result", "")
    except (json.JSONDecodeError, ValueError, AttributeError) as exc:
        raise RuntimeError(f"could not parse claude CLI output: {exc}") from exc
    return (result or "").strip()
