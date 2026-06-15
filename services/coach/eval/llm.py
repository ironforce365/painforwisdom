"""Subscription-backed LLM caller.

Rides Gonzalo's Max plan via the claude CLI headless mode (no Anthropic API key).
See memory: subscription_cli_judge. The single LLM seam for the eval package —
tests monkeypatch ``subprocess`` (here) or ``call_llm`` (in callers) so logic
runs offline.
"""
from __future__ import annotations

import json
import subprocess

from shared.perf import perf_step

DEFAULT_MODEL = "claude-sonnet-4-6"
_TIMEOUT_S = 120


class LLMError(RuntimeError):
    """Raised when the claude CLI call fails or returns an API error."""


def call_llm(*, system: str, user: str, model: str = DEFAULT_MODEL) -> str:
    """Call ``claude -p`` headless and return the model's text output.

    The CLI has no separate system-prompt flag in ``-p`` mode, so we frame the
    system instructions ahead of the user content in a single prompt.
    """
    prompt = f"{system}\n\n---\n\n{user}" if system else user
    cmd = ["claude", "-p", prompt, "--output-format", "json", "--model", model]
    try:
        # The single most diagnostic number for turn latency: each call is a full
        # `claude -p` CLI cold-start. Timing it tells us per-call cost AND how many
        # LLM calls a turn fires (judge = 1; + 1 per demoted claim).
        with perf_step("llm_subprocess", model=model):
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=_TIMEOUT_S)
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        raise LLMError(f"claude CLI call failed: {e}") from e
    if proc.returncode != 0:
        raise LLMError(f"claude CLI exit {proc.returncode}: {proc.stderr[:300]}")
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        raise LLMError(f"non-JSON CLI output: {proc.stdout[:300]}") from e
    if payload.get("is_error"):
        raise LLMError(f"CLI api error: {payload.get('api_error_status')}")
    return payload.get("result", "")
