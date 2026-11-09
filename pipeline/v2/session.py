"""Claude Agent SDK session runner and call_llm backend (spec 2026-11-09-session-pipeline-v2 §2)."""
from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence

import claude_agent_sdk as sdk

from pipeline.usage import record_end, record_error, record_start

log = logging.getLogger(__name__)

BILLING_MODE = "subscription-session"
RESEARCH_TOOLS = ("WebSearch", "WebFetch")
RESEARCH_MAX_TURNS = 30
MAX_RETRIES = 4
BASE_BACKOFF_S = 4.0
_TRANSIENT_STATUS = {429, 500, 502, 503, 504, 529}

_sleep = time.sleep  # tests patch this


@dataclass
class SessionResult:
    text: str
    session_id: Optional[str]
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    notional_cost_usd: float = 0.0
    duration_s: float = 0.0
    num_turns: int = 0
    stop_reason: Optional[str] = None
    messages: List[Dict[str, Any]] = field(default_factory=list)
    sdk_version: str = sdk.__version__
    cli_version: Optional[str] = None
    attempts: int = 1

    def usage(self) -> Dict[str, int]:
        return {"input_tokens": self.input_tokens, "output_tokens": self.output_tokens,
                "cache_read_tokens": self.cache_read_tokens, "cache_creation_tokens": self.cache_creation_tokens}


class SessionError(RuntimeError):
    """Session ended with is_error and the SDK did not raise."""

    def __init__(self, subtype: Optional[str], detail: str):
        super().__init__(f"session {subtype}: {detail}"[:500])
        self.subtype = subtype


def _options(system_prompt: str, model: str, allowed_tools: Sequence[str], max_turns: int, cwd: str):
    tools = list(allowed_tools)
    return sdk.ClaudeAgentOptions(
        system_prompt=system_prompt,
        model=model,
        tools=tools,  # the only tools that exist in the session
        allowed_tools=tools,  # auto-approved; nothing else is
        setting_sources=[],
        max_turns=max_turns,
        cwd=cwd,
        env={"ANTHROPIC_API_KEY": ""},  # SDK merges os.environ; empty = unset for the CLI, bills the subscription
    )


def _to_dict(msg: Any) -> Dict[str, Any]:
    body = dataclasses.asdict(msg) if dataclasses.is_dataclass(msg) else {"repr": repr(msg)}
    return json.loads(json.dumps({"type": type(msg).__name__, **body}, default=str))


async def _collect(user_message: str, options) -> List[Any]:
    return [m async for m in sdk.query(prompt=user_message, options=options)]


def _run_once(system_prompt, user_message, model, allowed_tools, max_turns, cwd) -> SessionResult:
    t0 = time.time()
    msgs = asyncio.run(_collect(user_message, _options(system_prompt, model, allowed_tools, max_turns, cwd)))
    duration = time.time() - t0
    result = next((m for m in reversed(msgs) if isinstance(m, sdk.ResultMessage)), None)
    if result is None:
        raise SessionError(None, "no ResultMessage")
    if result.is_error:
        raise SessionError(result.subtype, str(result.errors or result.result))
    cli_version = next((m.data.get("claude_code_version") for m in msgs
                        if isinstance(m, sdk.SystemMessage) and m.subtype == "init"), None)
    usage = result.usage or {}
    return SessionResult(
        text=(result.result or "").strip(),
        session_id=result.session_id,
        model=model,
        input_tokens=int(usage.get("input_tokens") or 0),
        output_tokens=int(usage.get("output_tokens") or 0),
        cache_read_tokens=int(usage.get("cache_read_input_tokens") or 0),
        cache_creation_tokens=int(usage.get("cache_creation_input_tokens") or 0),
        notional_cost_usd=float(result.total_cost_usd or 0.0),
        duration_s=duration,
        num_turns=result.num_turns,
        stop_reason=result.stop_reason,
        messages=[_to_dict(m) for m in msgs],
        cli_version=cli_version,
    )


def _is_transient(exc: BaseException) -> bool:
    if isinstance(exc, sdk.CLINotFoundError):
        return False
    if isinstance(exc, sdk.ResultError):
        text = f"{exc.result} {exc.errors}".lower()
        return (exc.terminal_reason == "api_error" or exc.api_error_status in _TRANSIENT_STATUS
                or "rate limit" in text or "overloaded" in text)
    if isinstance(exc, SessionError):
        return False
    return isinstance(exc, (sdk.ProcessError, sdk.CLIConnectionError, sdk.CLIJSONDecodeError, ConnectionError))


def run_session(system_prompt: str, user_message: str, *, model: str, allowed_tools: Sequence[str] = (),
                max_turns: int, cwd: str) -> SessionResult:
    attempt = 0
    while True:
        call_id = record_start(model)
        try:
            res = _run_once(system_prompt, user_message, model, allowed_tools, max_turns, cwd)
        except Exception as exc:
            record_error(call_id, exc)
            if attempt < MAX_RETRIES and _is_transient(exc):
                _sleep(BASE_BACKOFF_S * (2 ** attempt))
                attempt += 1
                continue
            raise
        record_end(call_id, res.usage(), 0.0, res.duration_s)
        res.attempts = attempt + 1
        return res


def session_backend(on_session: Callable[[SessionResult, Dict[str, Any]], None], *,
                    cwd: str) -> Callable[..., Dict[str, Any]]:
    def backend(*, model: str, system_prompt: str, user_message: str, max_tokens: int,
                web_search: bool = False, tools: Any = None) -> Dict[str, Any]:
        allowed = RESEARCH_TOOLS if web_search else ()
        res = run_session(system_prompt, user_message, model=model, allowed_tools=allowed,
                          max_turns=RESEARCH_MAX_TURNS if web_search else 1, cwd=cwd)
        if res.output_tokens > max_tokens:  # SDK has no max_tokens; never truncate
            log.warning("session %s output_tokens %d > requested max_tokens %d",
                        res.session_id, res.output_tokens, max_tokens)
        request = {"system_prompt": system_prompt, "user_message": user_message, "model": model,
                   "tools": list(allowed), "max_tokens": max_tokens}
        try:
            on_session(res, request)
        except Exception:  # audit writes never fail a run
            log.exception("on_session failed for session %s", res.session_id)
        return {"text": res.text, "duration_s": res.duration_s, **res.usage(), "cost_usd": 0.0,
                "billing_mode": BILLING_MODE, "model": res.model, "stop_reason": res.stop_reason,
                "attempts": res.attempts}

    return backend


class FakeSession:
    """Scripted run_session stand-in: install with monkeypatch.setattr(session, "run_session", fake)."""

    def __init__(self, *texts_or_results: Any):
        self._script = list(texts_or_results)
        self.calls: List[Dict[str, Any]] = []

    def __call__(self, system_prompt: str, user_message: str, *, model: str, allowed_tools: Sequence[str] = (),
                 max_turns: int, cwd: str) -> SessionResult:
        self.calls.append({"system_prompt": system_prompt, "user_message": user_message, "model": model,
                           "allowed_tools": tuple(allowed_tools), "max_turns": max_turns, "cwd": cwd})
        item = self._script.pop(0)
        if isinstance(item, BaseException):
            raise item
        if isinstance(item, SessionResult):
            return item
        return SessionResult(text=item, session_id=f"fake-{len(self.calls)}", model=model, num_turns=1,
                             stop_reason="end_turn", cli_version="fake")
