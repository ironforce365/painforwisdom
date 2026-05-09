"""LiteLLM-backed Anthropic wrapper for the PoC.

Auth precedence (LiteLLM Anthropic provider):
1. ANTHROPIC_AUTH_TOKEN  -> Bearer auth, bills against Pro/Max subscription quota.
   Set via `claude setup-token`. Anthropic's OAuth path requires:
     - header `anthropic-beta: oauth-2025-04-20`
     - first system block exactly: "You are Claude Code, Anthropic's official CLI for Claude."
   Without both, Anthropic returns a (mislabeled) 429 rate_limit_error.
   Mechanism: LiteLLM's `client=` kwarg accepts a pre-built `anthropic.Anthropic`
   client. We construct it with `auth_token=` and `default_headers=`, then let
   LiteLLM dispatch through it. This bypasses LiteLLM's header builder (which
   would force `x-api-key`) while keeping LiteLLM's cost-tracking, retry, and
   eventual cross-provider fallback machinery in the call path.
2. ANTHROPIC_API_KEY     -> standard `x-api-key`, bills API credits.
"""
from __future__ import annotations

import os
import time
from typing import Any, Dict, List, Optional

import litellm
from anthropic import Anthropic


CLAUDE_CODE_IDENTITY = "You are Claude Code, Anthropic's official CLI for Claude."

# Cached client + billing mode so repeated calls in one process reuse the same
# httpx connection pool (faster) and don't re-read the env every time.
_CLIENT: Optional[Anthropic] = None
_BILLING_MODE: Optional[str] = None


def _get_client() -> tuple[Anthropic, str]:
    global _CLIENT, _BILLING_MODE
    if _CLIENT is not None and _BILLING_MODE is not None:
        return _CLIENT, _BILLING_MODE
    if os.getenv("ANTHROPIC_AUTH_TOKEN"):
        _CLIENT = Anthropic(
            auth_token=os.environ["ANTHROPIC_AUTH_TOKEN"],
            default_headers={"anthropic-beta": "oauth-2025-04-20"},
        )
        _BILLING_MODE = "subscription"
        return _CLIENT, _BILLING_MODE
    if os.getenv("ANTHROPIC_API_KEY"):
        _CLIENT = Anthropic()
        _BILLING_MODE = "api-credits"
        return _CLIENT, _BILLING_MODE
    raise RuntimeError(
        "Set ANTHROPIC_AUTH_TOKEN (Pro/Max subscription via `claude setup-token`) "
        "or ANTHROPIC_API_KEY (API credits) in the environment or .env"
    )


def _build_messages(system_prompt: str, user_message: str, billing_mode: str) -> List[Dict[str, Any]]:
    """Build OpenAI-format messages with Anthropic cache_control on the
    extractor-body system block. LiteLLM passes cache_control through to the
    Anthropic API when the system content is sent as a list of typed blocks."""
    if billing_mode == "subscription":
        system_blocks: List[Dict[str, Any]] = [
            {"type": "text", "text": CLAUDE_CODE_IDENTITY},
            {"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}},
        ]
    else:
        system_blocks = [
            {"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}},
        ]
    return [
        {"role": "system", "content": system_blocks},
        {"role": "user", "content": user_message},
    ]


def _extract_usage(response: Any) -> Dict[str, int]:
    """Pull token counts from a LiteLLM response, including the Anthropic
    cache fields surfaced via prompt_tokens_details / cache_creation_input_tokens."""
    usage = getattr(response, "usage", None) or {}
    if hasattr(usage, "model_dump"):
        usage = usage.model_dump()
    elif not isinstance(usage, dict):
        usage = vars(usage)
    input_tokens = usage.get("prompt_tokens") or usage.get("input_tokens") or 0
    output_tokens = usage.get("completion_tokens") or usage.get("output_tokens") or 0
    cache_read = (
        usage.get("cache_read_input_tokens")
        or (usage.get("prompt_tokens_details") or {}).get("cached_tokens", 0)
        or 0
    )
    cache_creation = usage.get("cache_creation_input_tokens", 0) or 0
    return {
        "input_tokens": int(input_tokens),
        "output_tokens": int(output_tokens),
        "cache_read_tokens": int(cache_read),
        "cache_creation_tokens": int(cache_creation),
    }


def call_llm(
    model: str,
    system_prompt: str,
    user_message: str,
    max_tokens: int = 2000,
) -> Dict[str, Any]:
    """One call through LiteLLM, dispatched via the pre-built Anthropic client.
    Returns text + usage + cost (LiteLLM's pricing tables; subscription billing
    is reported as $0)."""
    client, billing_mode = _get_client()
    messages = _build_messages(system_prompt, user_message, billing_mode)
    t0 = time.time()
    resp = litellm.completion(
        model=f"anthropic/{model}",
        messages=messages,
        max_tokens=max_tokens,
        client=client,
    )
    duration = time.time() - t0

    choice = resp.choices[0]
    text = (choice.message.content or "").strip()
    usage = _extract_usage(resp)

    if billing_mode == "subscription":
        cost_usd = 0.0
    else:
        try:
            cost_usd = float(litellm.completion_cost(completion_response=resp))
        except Exception:
            cost_usd = 0.0

    return {
        "text": text,
        "duration_s": duration,
        **usage,
        "cost_usd": cost_usd,
        "billing_mode": billing_mode,
        "model": model,
        "stop_reason": getattr(choice, "finish_reason", None),
    }
