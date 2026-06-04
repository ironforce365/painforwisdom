"""Spin up an athlete-agent (Sonnet 4.6) that role-plays a profile and chats with the coach service."""
from __future__ import annotations
from pathlib import Path
import yaml
import httpx
import anthropic


def _athlete_reply(profile: dict, history: list[dict]) -> str:
    client = anthropic.Anthropic()
    sys = (
        f"You are role-playing a runner. Stay in character.\n\n"
        f"NAME: {profile['name']}\n"
        f"BACKSTORY:\n{profile['backstory']}\n\n"
        f"STYLE:\n{profile['style']}\n\n"
        f"Respond as the runner would, conversationally, 1-3 sentences."
    )
    resp = client.messages.create(
        model="claude-sonnet-4-6", max_tokens=400, system=sys, messages=history,
    )
    return resp.content[0].text


def simulate(profile_path: Path, coach_url: str) -> list[dict]:
    profile = yaml.safe_load(profile_path.read_text())
    user_id = f"sim-{profile['slug']}"
    transcript: list[dict] = [{"role": "user", "content": profile["opener"]}]
    for _ in range(profile["turn_count"]):
        r = httpx.post(f"{coach_url}/turn", json={"user_id": user_id, "text": transcript[-1]["content"]}, timeout=120)
        r.raise_for_status()
        coach_reply = r.json()["reply"]
        transcript.append({"role": "assistant", "content": coach_reply})
        next_athlete = _athlete_reply(profile, transcript)
        transcript.append({"role": "user", "content": next_athlete})
    return transcript
