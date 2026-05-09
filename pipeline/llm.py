"""LiteLLM-backed Anthropic wrapper for the full pipeline.

LiteLLM 1.83+ accepts `ANTHROPIC_AUTH_TOKEN` directly via env var. Earlier
versions (e.g. 1.55 used in the PoC) required passing a pre-built Anthropic
client via `client=`. We dropped that pattern: in 1.83 it caused 401s
because LiteLLM's internal HTTP path no longer reads the SDK client's auth
state. The simpler env-var path works.

Auth path:
  - ANTHROPIC_AUTH_TOKEN -> Bearer (subscription, $0 marginal). Token
    rotation: read from ~/.claude/.credentials.json on every call (cheap,
    small JSON file) and re-export to env so a refresh by `claude` CLI is
    picked up automatically. Re-read again on auth-error retry.
  - ANTHROPIC_API_KEY    -> x-api-key (API credits).

Subscription path requires:
  - extra_headers={"anthropic-beta": "oauth-2025-04-20"}
  - first system block exactly: CLAUDE_CODE_IDENTITY
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import litellm

from pipeline.token_rotation import read_oauth_token


CLAUDE_CODE_IDENTITY = "You are Claude Code, Anthropic's official CLI for Claude."

# Track which billing mode is active so the system-block builder knows
# whether to prepend the Claude Code identity (subscription only).
_BILLING_MODE: Optional[str] = None


def _refresh_auth() -> str:
    """Sync env with the freshest available token; return billing mode.

    Subscription path: read ~/.claude/.credentials.json each call. If the
    `claude` CLI rotated the token, the next API request uses the new value.
    API path: ANTHROPIC_API_KEY is left untouched.
    """
    global _BILLING_MODE
    fresh = read_oauth_token()
    if fresh:
        # Always overwrite — credentials.json is the source of truth when present.
        os.environ["ANTHROPIC_AUTH_TOKEN"] = fresh
        # Drop a stale x-api-key that would otherwise short-circuit LiteLLM auth.
        os.environ.pop("ANTHROPIC_API_KEY", None)
        _BILLING_MODE = "subscription"
        return "subscription"
    if os.getenv("ANTHROPIC_AUTH_TOKEN"):
        _BILLING_MODE = "subscription"
        return "subscription"
    if os.getenv("ANTHROPIC_API_KEY"):
        _BILLING_MODE = "api-credits"
        return "api-credits"
    raise RuntimeError(
        "No Anthropic auth available. Run `claude setup-token` for subscription, "
        "or set ANTHROPIC_API_KEY for API credits."
    )


def _build_messages(
    system_prompt: str, user_message: str, billing_mode: str
) -> List[Dict[str, Any]]:
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


def _is_auth_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return (
        "invalid authentication credentials" in msg
        or "authenticationerror" in msg
        or "unauthorized" in msg
    )


def _is_rate_limit(exc: Exception) -> bool:
    msg = str(exc).lower()
    return "rate_limit" in msg or "ratelimiterror" in msg or "429" in msg


def call_llm(
    model: str,
    system_prompt: str,
    user_message: str,
    max_tokens: int = 2000,
    tools: Optional[List[Dict[str, Any]]] = None,
    web_search: bool = False,
    max_retries: int = 4,
    base_backoff_s: float = 4.0,
) -> Dict[str, Any]:
    """One LiteLLM call. Auth via env (refreshed from credentials.json each call).

    On a transient auth error we re-read the token from disk and retry once
    immediately (claude CLI may have rotated it mid-run). On rate-limit we
    exponentially back off.
    """
    effective_tools: List[Dict[str, Any]] = list(tools or [])
    if web_search and not any(t.get("type", "").startswith("web_search") for t in effective_tools):
        effective_tools.append({"type": "web_search_20250305", "name": "web_search"})

    attempt = 0
    last_exc: Optional[Exception] = None

    while attempt <= max_retries:
        billing_mode = _refresh_auth()
        messages = _build_messages(system_prompt, user_message, billing_mode)
        kwargs: Dict[str, Any] = {
            "model": f"anthropic/{model}",
            "messages": messages,
            "max_tokens": max_tokens,
        }
        if billing_mode == "subscription":
            kwargs["extra_headers"] = {"anthropic-beta": "oauth-2025-04-20"}
        if effective_tools:
            kwargs["tools"] = effective_tools

        t0 = time.time()
        try:
            resp = litellm.completion(**kwargs)
            duration = time.time() - t0
            choice = resp.choices[0]
            content = choice.message.content
            if isinstance(content, list):
                text_parts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
                text = "\n".join(p for p in text_parts if p).strip()
            else:
                text = (content or "").strip()
            usage = _extract_usage(resp)
            cost_usd = 0.0
            if billing_mode != "subscription":
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
                "attempts": attempt + 1,
            }
        except Exception as exc:
            last_exc = exc
            if _is_auth_error(exc) and attempt < max_retries:
                # Token may have rotated — re-read on next loop iteration.
                attempt += 1
                continue
            if _is_rate_limit(exc) and attempt < max_retries:
                wait = base_backoff_s * (2 ** attempt)
                time.sleep(wait)
                attempt += 1
                continue
            raise

    assert last_exc is not None
    raise last_exc


def append_metric(jsonl_path: Path, row: Dict[str, Any]) -> Dict[str, Any]:
    jsonl_path.parent.mkdir(parents=True, exist_ok=True)
    with jsonl_path.open("a") as f:
        f.write(json.dumps(row) + "\n")
    return row
