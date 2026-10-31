"""FastAPI service: POST /turn (user_id, text) → coach reply.

Wires together:
- crisis pre-filter
- claude-agent-sdk client with 3 MCP servers (vault_rag, user_memory, mem0)
- post-turn inbox hook
"""
from __future__ import annotations
import asyncio
import contextlib
import contextvars
import json
import logging
import os
import re
import socket
from pathlib import Path
from typing import AsyncIterator, Tuple

from claude_agent_sdk import (
    AssistantMessage,
    StreamEvent,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    create_sdk_mcp_server,
    tool,
)
from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from agent.failures import conn_trouble_reply, is_api_error_reply
from agent.hooks import write_inbox_entry
from agent.memory import delete_user_memory, read_user_memory, write_user_memory
from agent.prompts import compose_system_prompt
from agent.retrieval import (
    retrieve_doctrine_for_turn,
    retrieve_for_turn_rich,
    search_vault_warm,
    warm_start,
)
from agent.session_map import SessionMap
from crisis.filter import check_message
from shared.perf import perf_count, perf_step

# Per-turn slug -> retrieved chunk text, bound fresh by each endpoint and filled
# by `_compose_turn_prompt`, so the grounding judge can verify entailment against
# real source text (Stream 2 plumbing). ContextVar for the same reason as
# `_stream_sources`: per-task isolation; fakes that bypass composition simply
# leave it empty.
_turn_slug_text: contextvars.ContextVar[dict] = contextvars.ContextVar(
    "_turn_slug_text", default={}
)

# Doctrine + user-memory world (Stream 3): the joined source text for this turn's
# D1 (doctrine) and M1 (memory) grounding sources, bound fresh per endpoint and
# filled by `_compose_turn_prompt`. ContextVar for per-task isolation, same as
# `_turn_slug_text`.
_turn_doctrine_text: contextvars.ContextVar[str] = contextvars.ContextVar(
    "_turn_doctrine_text", default=""
)
_turn_memory_text: contextvars.ContextVar[str] = contextvars.ContextVar(
    "_turn_memory_text", default=""
)

# Grounding gate (Stream 0) + validation loop (Stream 2), default OFF via
# COACH_GROUNDING_GATE. Import-guarded so a problem in the eval package can never
# break the coach's import or a live turn. With the flag OFF every hook here is a
# no-op and the coach behaves exactly as before.
try:
    from eval.grounding.integration import (
        detect_validation_signals,
        gate_enabled,
        maybe_gate,
    )
    from eval.grounding.types import KIND_DOCTRINE, KIND_MEMORY
    from eval.grounding.types import Source as _GroundingSource

    def _slugs_to_sources(slugs: list[str]) -> list["_GroundingSource"]:
        # Doctrine/memory world (Stream 3): a doctrine source (D1, tier 2) and a
        # user-memory source (M1, tier 1) built from the text stashed this turn.
        # The gate then enforces: a FACT about the user needs M1 (memory) — a
        # doctrine source can never warrant biography.
        dt = _turn_doctrine_text.get()
        mt = _turn_memory_text.get()
        if dt or mt:
            out: list["_GroundingSource"] = []
            if dt:
                out.append(_GroundingSource(id="D1", tier=2, kind=KIND_DOCTRINE, text=dt))
            if mt:
                out.append(_GroundingSource(id="M1", tier=1, kind=KIND_MEMORY, text=mt))
            return out
        # Legacy (flag-off / no doctrine+memory captured): single S1 from vault
        # chunk text, or text-less per-slug sources for fakes / no retrieval.
        texts = _turn_slug_text.get()
        joined = "\n\n".join(texts[s] for s in slugs if s in texts)
        if joined:
            return [_GroundingSource(id="S1", tier=1, kind="vault_context", text=joined)]
        return [_GroundingSource(id=s, tier=1, kind="vault_entry", text="") for s in slugs]
except Exception:  # noqa: BLE001 - never let eval-package issues break the service

    def gate_enabled() -> bool:
        return False

    def maybe_gate(reply: str, **_kw) -> str:
        return reply

    def detect_validation_signals(user_text: str, **_kw) -> list:
        return []

    def _slugs_to_sources(slugs: list[str]) -> list:
        return []


# Coaching-topic + no-file guardrail (F4). Same import-guard + default-OFF
# contract as the grounding gate. Runs in parallel with maybe_gate so it adds no
# serial latency. A clean reply passes through untouched; a blocked reply is
# replaced by a localized coaching redirect.
try:
    from eval.topic_guard import guard_enabled, maybe_guard
except Exception:  # noqa: BLE001 - never let eval-package issues break the service

    def guard_enabled() -> bool:
        return False

    def maybe_guard(reply: str, **_kw) -> str:
        return reply


# Namespaced MCP tool name. The claude-agent-sdk MCP tool naming convention is
# `mcp__<server_key>__<tool>` where `<server_key>` is the key in the
# `mcp_servers` dict passed to ClaudeAgentOptions (NOT the FastMCP constructor
# name).
SEARCH_VAULT_TOOL = "mcp__vault_rag__search_vault"

@contextlib.asynccontextmanager
async def _lifespan(_app: "FastAPI"):
    """Build the retriever singletons (index + BM25 + cross-encoder) at boot so the
    ~12–15s cold build no longer lands on the first user after every deploy /
    watchdog restart (R4). Run in a worker thread so startup — and the shallow
    /health the watchdog polls — stays responsive while it builds; a first turn
    that beats the warm-up just blocks on the same lock instead of racing it."""
    _detach(asyncio.to_thread(warm_start), "warm_start")
    yield


app = FastAPI(title="painforwisdom-coach", lifespan=_lifespan)
_sessions = SessionMap()
log = logging.getLogger("coach.service")


def _stream_budget_s() -> float:
    """Hard wall-clock budget for agent generation (2026-07-04 outage: the SDK
    retried an unreachable API for 191s, past every client deadline). Read
    per-call so ops can tune live; must stay below the bot's read timeout with
    room for gate/guard (≤ COACH_LLM_TIMEOUT_S) on top."""
    return float(os.environ.get("COACH_STREAM_BUDGET_S", "150"))


# Model + effort pinning (2026-07-04 turn-latency review, R1). Prod ran on the
# CLI's silent default (sonnet-4-6) with headless default effort=high; the A/B in
# docs/2026-07-04-turn-latency-review.md showed sonnet-5 @ effort=medium beats
# that baseline on every rubric dimension at ~half the generation latency. Both
# env-tunable so switching model (e.g. an Opus A/B week) or bumping effort back to
# high is a config change, never a deploy.
_EFFORTS = {"low", "medium", "high", "xhigh", "max"}


def _agent_model() -> str | None:
    """Model for the coaching agent. Empty/unset → CLI default (no pin)."""
    return os.environ.get("COACH_AGENT_MODEL", "claude-sonnet-5").strip() or None


def _agent_effort() -> str | None:
    """Reasoning effort for the coaching agent (low|medium|high|xhigh|max).

    Trims thinking depth in every round-trip — the reply-step latency lever the
    user was willing to pull. Invalid values fall back to the CLI default rather
    than crashing a turn."""
    v = os.environ.get("COACH_AGENT_EFFORT", "medium").strip().lower()
    if v in _EFFORTS:
        return v
    if v not in ("", "none", "default"):
        log.warning("ignoring invalid COACH_AGENT_EFFORT=%r (use one of %s)", v, sorted(_EFFORTS))
    return None


def _rationale_enabled() -> bool:
    """Whether to stream the model's thinking as live 'rationale' to the client
    (the 'story' UX). Default ON; a kill-switch (COACH_STREAM_RATIONALE=0) reverts
    to answer-only streaming if the thinking channel ever misbehaves in prod."""
    return os.environ.get("COACH_STREAM_RATIONALE", "true").strip().lower() in _DEBUG_TRUTHY


# Fire-and-forget background work (memory write, validation bookkeeping) kept off
# the reply path (R3/R5). A module-level set holds a strong reference so the task
# isn't garbage-collected mid-flight (asyncio only holds a weak ref); the
# done-callback discards it. Every detached callable already swallows its own
# failures, so a dead task can never surface to a user.
_bg_tasks: set[asyncio.Task] = set()


def _detach(coro, label: str) -> None:
    task = asyncio.create_task(coro, name=label)
    _bg_tasks.add(task)
    task.add_done_callback(_bg_tasks.discard)


# Rationale sanitizer: the streamed thinking is the model's reasoning, not its
# vetted reply, so it never passes the grounding gate. Two structural facts keep
# this safe: (1) source slugs are never in the model's context (retrieval blocks
# omit them), so thinking cannot leak a citation; (2) the only internal scaffolding
# that CAN appear is the [[claim ...]] tagging protocol and the <doctrine> /
# <about_this_user> / <vault_context> markers — which we strip here. Because
# thinking arrives token-by-token, a tag can split across chunks, so we hold back
# any trailing unclosed "[[" / "<" until it completes.
_CLAIM_TAG_RE = re.compile(r"\[\[[^\]]*\]\]")
_PROTOCOL_TAG_RE = re.compile(
    r"</?(?:doctrine|about_this_user|vault_context|user_id|proactive_outreach)\b[^>]*>"
)


def _strip_protocol(text: str) -> str:
    return _PROTOCOL_TAG_RE.sub("", _CLAIM_TAG_RE.sub("", text))


class _RationaleSanitizer:
    """Rolling sanitizer for streamed thinking. ``feed`` returns the portion safe
    to emit now (holding back an incomplete trailing tag); ``flush`` returns the
    rest at stream end."""

    def __init__(self) -> None:
        self._buf = ""

    def _safe_cut(self) -> int:
        # Hold back from the last unclosed "[[" or "<" so a tag split across
        # chunks is never emitted half-stripped.
        cut = len(self._buf)
        for opener, closer in (("[[", "]]"), ("<", ">")):
            i = self._buf.rfind(opener)
            if i != -1 and self._buf.find(closer, i) == -1:
                cut = min(cut, i)
        return cut

    def feed(self, chunk: str) -> str:
        self._buf += chunk
        cut = self._safe_cut()
        emit, self._buf = self._buf[:cut], self._buf[cut:]
        return _strip_protocol(emit)

    def flush(self) -> str:
        out, self._buf = _strip_protocol(self._buf), ""
        return out

# Per-stream side channel for source slugs collected during `_stream_agent`.
# `_stream_agent` populates the list bound here; the endpoint reads it after the
# stream completes to write the inbox entry. ContextVar keeps it isolated per
# async task (concurrent streams don't clobber each other), and a monkeypatched
# fake streamer simply leaves the default empty list in place.
_stream_sources: contextvars.ContextVar[list[str]] = contextvars.ContextVar(
    "_stream_sources", default=[]
)


class Turn(BaseModel):
    user_id: str
    text: str
    # Telegram client language (e.g. "es", "en-US"); drives the topic-guard
    # redirect language. Optional so older callers/tests keep working.
    language_code: str | None = None
    # "live" (real user) or "test" (synthetic harness). A test turn gets a real
    # coaching reply but writes NO vault inbox entry, so synthetic conversations
    # never feed the content pipeline / knowledge base.
    channel: str = "live"


class Reply(BaseModel):
    reply: str
    crisis: bool


class ResetRequest(BaseModel):
    user_id: str


class OutreachRequest(BaseModel):
    user_id: str
    # "inactivity" (1-day quiet check-in) or "followup" (circle back on an open
    # loop). Drives the directive; defaults to a plain inactivity check-in.
    kind: str = "inactivity"
    language_code: str | None = None
    # The coach's last message, included so a follow-up can name what was left open.
    last_coach_text: str | None = None


def _extract_source_slugs(content) -> list[str]:
    """Pull source slugs from a search_vault tool result.

    The SDK's ToolResultBlock.content can take several shapes depending on the
    FastMCP version that produced it:

    - A list of dicts (FastMCP structured return), e.g.
      `[{"text": ..., "source": <slug>, "score": ...}, ...]`
    - A list wrapping the structured payload as text, e.g.
      `[{"type": "text", "text": "<json>"}]`
    - A bare JSON-encoded string.
    """
    if isinstance(content, str):
        try:
            content = json.loads(content)
        except (json.JSONDecodeError, ValueError):
            return []
    if not isinstance(content, list):
        return []
    slugs: list[str] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        if "source" in item:
            slugs.append(str(item["source"]))
            continue
        if item.get("type") == "text":
            try:
                inner = json.loads(item.get("text", ""))
            except (json.JSONDecodeError, ValueError):
                continue
            if isinstance(inner, list):
                for sub in inner:
                    if isinstance(sub, dict) and "source" in sub:
                        slugs.append(str(sub["source"]))
    return slugs


async def _walk_messages(
    messages: AsyncIterator, sources: list[str]
) -> AsyncIterator[str]:
    """Walk an async iterator of SDK messages, yielding assistant text chunks in
    order and appending source slugs (from search_vault tool results) into the
    caller-provided `sources` list as they are discovered.

    Shared by both the blocking `_extract_reply_and_sources` collector and the
    streaming `_stream_agent` so the SDK message-walking lives in one place.
    """
    search_vault_tool_use_ids: set[str] = set()

    async for msg in messages:
        if not hasattr(msg, "content"):
            continue
        content = msg.content
        # UserMessage.content may be a bare string; only iterable block lists
        # carry tool-use / tool-result information we care about.
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, TextBlock):
                yield block.text
            elif isinstance(block, ToolUseBlock) and block.name == SEARCH_VAULT_TOOL:
                search_vault_tool_use_ids.add(block.id)
            elif isinstance(block, ToolResultBlock) and block.tool_use_id in search_vault_tool_use_ids:
                sources.extend(_extract_source_slugs(block.content))


async def _extract_reply_and_sources(messages: AsyncIterator) -> Tuple[str, list[str]]:
    """Walk an async iterator of SDK messages, concatenating assistant text
    and collecting source slugs from search_vault tool results.

    Pulled out of `_chat_with_agent` so the parsing logic can be unit-tested
    without spinning up a real ClaudeSDKClient.
    """
    sources: list[str] = []
    reply_chunks: list[str] = []
    async for chunk in _walk_messages(messages, sources):
        reply_chunks.append(chunk)
    return "".join(reply_chunks), sources


async def _walk_stream(
    messages: AsyncIterator, sources: list[str], counter: dict
) -> AsyncIterator:
    """Streaming walk for the 'story' UX (R7). Yields two kinds of item:

    - ``("thinking", <text>)`` — the model's reasoning, streamed live as it
      generates (from ``thinking_delta`` partial events, or a whole ThinkingBlock
      as a fallback if the CLI didn't emit deltas). This is the rationale the user
      watches while the answer is still forming.
    - ``<str>`` — assistant answer text, taken from the assembled TextBlock so the
      buffered gate always sees the authoritative reply (text_delta partials are
      ignored to avoid double-counting).

    Source slugs from search_vault tool results are appended into ``sources``;
    ``counter['assistant_messages']`` counts model round-trips for the
    ``agent_roundtrips`` perf line.
    """
    search_vault_tool_use_ids: set[str] = set()
    saw_thinking_delta = False

    async for msg in messages:
        if isinstance(msg, StreamEvent):
            ev = msg.event or {}
            if ev.get("type") == "content_block_delta":
                delta = ev.get("delta") or {}
                if delta.get("type") == "thinking_delta":
                    piece = delta.get("thinking") or ""
                    if piece:
                        saw_thinking_delta = True
                        yield ("thinking", piece)
            continue
        content = getattr(msg, "content", None)
        if not isinstance(content, list):
            continue
        if isinstance(msg, AssistantMessage):
            counter["assistant_messages"] = counter.get("assistant_messages", 0) + 1
        for block in content:
            if isinstance(block, TextBlock):
                yield block.text
            elif isinstance(block, ThinkingBlock):
                # Fallback path: no partial deltas were emitted, so surface the
                # whole thinking block once (arrives with the answer — no latency
                # win, but the story still renders).
                if not saw_thinking_delta and block.thinking:
                    yield ("thinking", block.thinking)
            elif isinstance(block, ToolUseBlock) and block.name == SEARCH_VAULT_TOOL:
                search_vault_tool_use_ids.add(block.id)
            elif isinstance(block, ToolResultBlock) and block.tool_use_id in search_vault_tool_use_ids:
                sources.extend(_extract_source_slugs(block.content))


def _retrieve_doctrine_timed(text: str):
    with perf_step("doctrine_retrieve"):
        return retrieve_doctrine_for_turn(text)


def _read_memory_timed(user_id: str, text: str):
    with perf_step("memory_read"):
        return read_user_memory(user_id, text)


async def _compose_turn_prompt(user_id: str, text: str) -> Tuple[str, list[str]]:
    """Build the agent query for a turn, injecting deterministically pre-retrieved
    context ahead of the user's message.

    Returns (query, pre_retrieved_slugs). The slugs seed the inbox source list so
    a turn is recorded as grounded even when the model never calls search_vault.
    The context block is placed before the user's text so the model reads its
    grounding first; it is omitted entirely when retrieval found nothing.

    Async so the two retrievals run in parallel worker threads (R6) and neither
    the OpenAI embedding round-trip nor the cross-encoder rerank blocks the event
    loop (R4). The ContextVar writes happen HERE, in the caller's task context —
    NOT inside the worker threads (a `to_thread` copy would swallow them, the same
    trap that forces `asyncio.timeout` over `wait_for` elsewhere in this file).
    """
    parts = [f"<user_id>{user_id}</user_id>"]
    if gate_enabled():
        # Doctrine + memory world: ground teaching on distilled doctrine (D1) and
        # facts about the user ONLY on conversation-derived memory (M1). The raw
        # vault is never retrieved at coach-time here — it is only distillation
        # input. Slugs (doctrine principles) still feed the inbox + debug canary.
        (doctrine_block, slugs, doctrine_text), (memory_block, memory_text) = await asyncio.gather(
            asyncio.to_thread(_retrieve_doctrine_timed, text),
            asyncio.to_thread(_read_memory_timed, user_id, text),
        )
        _turn_doctrine_text.set(doctrine_text)
        _turn_memory_text.set(memory_text)
        if doctrine_block:
            parts.append(doctrine_block)
        if memory_block:
            parts.append(memory_block)
        parts.append(text)
        return "\n\n".join(parts), slugs
    # Legacy (flag OFF): raw vault context, byte-identical to before.
    context_block, slugs, slug_text = await asyncio.to_thread(retrieve_for_turn_rich, text)
    # Stash chunk text for the grounding judge (endpoint binds a fresh dict).
    _turn_slug_text.get().update(slug_text)
    if context_block:
        parts.append(context_block)
    parts.append(text)
    return "\n\n".join(parts), slugs


def _merge_sources(*groups: list[str]) -> list[str]:
    """Union source slugs across groups, preserving first-seen order."""
    seen: set[str] = set()
    out: list[str] = []
    for group in groups:
        for slug in group:
            if slug not in seen:
                seen.add(slug)
                out.append(slug)
    return out


# Debug canary. When COACH_DEBUG is enabled the coach reply carries a footer
# naming the vault slugs that grounded the turn — proof it is leaning on the
# knowledge base rather than generic model knowledge. An empty slug list renders
# "(none)", which is the alarm: a grounded turn that retrieved nothing.
_DEBUG_TRUTHY = {"1", "true", "yes", "on"}


def _debug_enabled() -> bool:
    """Whether the COACH_DEBUG canary footer is on. Default-on for now: an unset
    var counts as enabled (read per-call so tests/ops can toggle live)."""
    return os.environ.get("COACH_DEBUG", "true").strip().lower() in _DEBUG_TRUTHY


def _debug_sources_footer(sources: list[str]) -> str:
    """Footer listing the grounding slugs, or '' when the canary is off.

    Presentation-only: appended to what the client sees, never written to the
    inbox. '(none)' is intentional — it surfaces a turn that grounded on nothing.
    """
    if not _debug_enabled():
        return ""
    body = ", ".join(sources) if sources else "(none)"
    return f"\n\n— kb sources: {body}"


@tool(
    "search_vault",
    "Search Gonzalo's coaching knowledge base. Returns up to 5 relevant chunks "
    "with source slugs. Call this BEFORE composing a reply on every turn.",
    {"query": str},
)
async def _search_vault_tool(args: dict) -> dict:
    """In-process `search_vault`: reuse the warm retriever singleton instead of
    letting the SDK spawn a fresh `python -m vault_rag.mcp_server` stdio
    subprocess every turn — which reloaded the cross-encoder reranker from cold
    (~30s) on each dig-deeper call. Gate ON => doctrine index (de-personalised);
    OFF => raw vault (legacy). Run the (CPU-bound) rerank off the event loop so a
    mid-turn tool call never stalls token streaming. Return shape mirrors the old
    stdio tool: the JSON list of {text, source, score} chunks."""
    query = (args or {}).get("query", "") or ""
    results = await asyncio.to_thread(search_vault_warm, query, doctrine=gate_enabled())
    return {"content": [{"type": "text", "text": json.dumps(results)}]}


# Built once per process. Same server name (`vault_rag`) and tool name
# (`search_vault`) as the old stdio server, so `allowed_tools`, the system
# prompt, and the model-facing `mcp__vault_rag__search_vault` contract are all
# unchanged — only the transport (in-process vs subprocess) differs.
_vault_rag_server = create_sdk_mcp_server(name="vault_rag", tools=[_search_vault_tool])


def _build_agent_options(user_id: str, *, partial: bool = False):
    """Construct ClaudeAgentOptions for a turn (model + effort + resume + vault
    tool).

    Shared by `_chat_with_agent` and `_stream_agent` so both run with identical
    session-resume and tooling configuration. ``partial=True`` (streaming path)
    turns on SDKPartialAssistantMessage events so the thinking channel can stream
    live (R7).

    Round-trip collapse (R2): only the in-process ``vault_rag`` tool is exposed.
    The old per-turn stdio ``user_memory`` + ``mem0`` MCP servers are gone —
    their reads are already pre-injected by `_compose_turn_prompt` (`<doctrine>` +
    `<about_this_user>`) and their writes happen service-side (`write_user_memory`),
    so the only thing exposing them did was cost the model deferred-schema
    ToolSearch round-trips plus redundant mid-turn re-searches of context it
    already had (measured: 4 of the turn's 5 inference round-trips).
    """
    from claude_agent_sdk import ClaudeAgentOptions

    # Resume an existing CLI session only if we have a real id from a prior turn.
    # Passing a fabricated id makes `claude --resume <id>` fail ("No conversation
    # found"); first turn must run with resume=None so the SDK creates the session.
    session_id = _sessions.get(user_id)
    kwargs = dict(
        # Claim contract appended ONLY when the gate is on; flag off => prompt
        # byte-identical to before, no [[claim]] tags can ever leak.
        system_prompt=compose_system_prompt(claims=gate_enabled()),
        resume=session_id,
        # Headless service: auto-approve the coach's own vault tool (server-level
        # pattern covers every tool it exposes). Without this the SDK's permission
        # gate denies the call and the agent can't reach the vault. Built-in tools
        # stay un-allowlisted → denied.
        allowed_tools=["mcp__vault_rag"],
        mcp_servers={
            # In-process server: no per-turn subprocess. The tool selects the
            # doctrine vs raw-vault index by gate state internally and reuses the
            # warm retriever singleton, so a dig-deeper never reloads the reranker
            # cold.
            "vault_rag": _vault_rag_server,
        },
        include_partial_messages=partial,
    )
    model = _agent_model()
    if model:
        kwargs["model"] = model
    effort = _agent_effort()
    if effort:
        kwargs["effort"] = effort
    return ClaudeAgentOptions(**kwargs)


def _sniff_session(msgs: AsyncIterator, captured: dict[str, str]) -> AsyncIterator:
    """Capture the SDK-assigned session_id (present on Assistant/Result messages)
    as messages stream past, so the next turn for this user can resume it."""
    async def _gen() -> AsyncIterator:
        async for m in msgs:
            sid = getattr(m, "session_id", None)
            if sid:
                captured["sid"] = sid
            yield m

    return _gen()


async def _chat_with_agent(user_id: str, text: str) -> Tuple[str, list[str]]:
    """Real implementation: spin up ClaudeSDKClient with MCP servers, call query,
    collect assistant text + retrieved source slugs. Monkeypatched in tests."""
    from claude_agent_sdk import ClaudeSDKClient

    options = _build_agent_options(user_id)
    captured: dict[str, str] = {}
    query, pre_slugs = await _compose_turn_prompt(user_id, text)

    with perf_step("agent_query"):
        async with ClaudeSDKClient(options=options) as client:
            await client.query(query)
            reply, tool_slugs = await _extract_reply_and_sources(
                _sniff_session(client.receive_response(), captured)
            )
    if captured.get("sid"):
        _sessions.set(user_id, captured["sid"])
    # Inbox records the deterministic pre-retrieval plus any slugs the model
    # pulled itself via search_vault, deduped.
    return reply, _merge_sources(pre_slugs, tool_slugs)


async def _stream_agent(user_id: str, text: str) -> AsyncIterator:
    """Streaming counterpart of `_chat_with_agent`: spin up ClaudeSDKClient and
    yield items as they arrive — bare-`str` answer chunks and
    ``("thinking", text)`` rationale tuples (see `_walk_stream`).

    Source slugs collected from search_vault tool results are appended into the
    `_stream_sources` ContextVar list so the endpoint can write them to the
    inbox once the stream completes.

    Monkeypatched in tests (fakes yield bare strings → treated as answer chunks,
    so they stay valid without knowing about the thinking channel).
    """
    from claude_agent_sdk import ClaudeSDKClient

    options = _build_agent_options(user_id, partial=True)
    captured: dict[str, str] = {}
    sources: list[str] = _stream_sources.get()
    query, pre_slugs = await _compose_turn_prompt(user_id, text)
    # Seed the inbox source sink with the deterministic pre-retrieval so the turn
    # is recorded as grounded even if the model adds no search_vault calls; the
    # endpoint dedupes before writing.
    sources.extend(pre_slugs)
    counter: dict = {}

    async with ClaudeSDKClient(options=options) as client:
        await client.query(query)
        async for item in _walk_stream(
            _sniff_session(client.receive_response(), captured), sources, counter
        ):
            yield item
    if captured.get("sid"):
        _sessions.set(user_id, captured["sid"])
    perf_count("agent_roundtrips", counter.get("assistant_messages", 0))


async def _gate_and_guard(
    reply_text: str,
    *,
    sources: list,
    user_id: str,
    thread_id: str,
    language_code: str | None,
    persist: bool = True,
) -> str:
    """Run the grounding gate and the coaching-topic guard concurrently.

    The topic guard inspects the ORIGINAL drafted reply; if it blocks (off-topic
    or a file offer) its localized redirect wins and the grounding result is
    discarded — off-topic content must not be shown even if it were grounded.
    Otherwise the grounding-gated text is returned. Both are full `claude -p`
    calls, so running them concurrently keeps the added latency at
    max(gate, guard) rather than their sum. Both default OFF, in which case each
    returns its input unchanged and this collapses to prior behaviour.
    """
    gated, guarded = await asyncio.gather(
        asyncio.to_thread(
            maybe_gate, reply_text, sources=sources, user_id=user_id,
            thread_id=thread_id, persist=persist,
        ),
        asyncio.to_thread(maybe_guard, reply_text, language_code=language_code),
    )
    if guarded != reply_text:  # guard blocked → redirect supersedes the gate
        return guarded
    return gated


@app.post("/turn", response_model=Reply)
async def turn(t: Turn, background: BackgroundTasks) -> Reply:
    hit = check_message(t.text)
    if hit is not None:
        return Reply(reply=hit.canned_reply, crisis=True)

    is_test = t.channel == "test"
    thread_id = _sessions.get(t.user_id) or t.user_id
    # Stream 2: match the incoming text against this thread's open validations
    # (questions the coach previously asked / reads it stated). Confirmations and
    # corrections are logged to the corpus and close their items. No-op when the
    # gate flag is off; failures are swallowed inside. Skipped entirely for
    # synthetic test turns — fabricated confirmations/corrections must never
    # enter the calibration corpus. R5: it's bookkeeping, so run it CONCURRENTLY
    # with generation (a worker thread) instead of serially before it, and join it
    # only just before the gate (which touches the same validations store).
    detect_task = None
    if not is_test:
        detect_task = asyncio.create_task(
            asyncio.to_thread(
                detect_validation_signals, t.text, thread_id=thread_id, user_id=t.user_id
            )
        )

    slug_text_token = _turn_slug_text.set({})
    doctrine_token = _turn_doctrine_text.set("")
    memory_token = _turn_memory_text.set("")
    try:
        with perf_step("turn_total", gate=gate_enabled()):
            try:
                with perf_step("agent_total"):
                    # asyncio.timeout, NOT wait_for: wait_for runs the coroutine
                    # in its own Task, whose context COPY would swallow the
                    # doctrine/memory ContextVars _compose_turn_prompt sets for
                    # the grounding gate. timeout() stays in this task's context.
                    async with asyncio.timeout(_stream_budget_s()):
                        reply_text, sources = await _chat_with_agent(t.user_id, t.text)
            except TimeoutError:
                # Generation blew the budget (unreachable API → SDK retry storm,
                # or a genuine hang). Answer honestly NOW, before the bot's read
                # deadline turns this into a "Still with you" mystery.
                log.error("turn budget exceeded (%.0fs); failing fast", _stream_budget_s())
                if detect_task:
                    detect_task.cancel()
                return Reply(reply=conn_trouble_reply(t.language_code), crisis=False)
            if is_api_error_reply(reply_text):
                # The draft IS a CLI API error, not coaching content. It must not
                # reach the gate/guard (two more doomed LLM calls), the inbox, or
                # the user (2026-07-04 outage).
                log.error("agent draft is an API error; failing fast: %s", reply_text[:200])
                if detect_task:
                    detect_task.cancel()
                return Reply(reply=conn_trouble_reply(t.language_code), crisis=False)
            # Join validation-detect before the gate: both touch the pending
            # validations store for this thread, so they must not write it
            # concurrently. Generation dominates, so this await is effectively free.
            if detect_task:
                await detect_task
            # Gate before persisting: when ON the gated text IS the reply (demotions
            # change content), so inbox and client must both see it. Footer stays last.
            with perf_step("gate_judge"):
                reply_text = await _gate_and_guard(
                    reply_text,
                    sources=_slugs_to_sources(sources),
                    user_id=t.user_id,
                    thread_id=thread_id,
                    language_code=t.language_code,
                    persist=not is_test,  # gate runs; corpus/validation writes don't
                )
            # Conversation-only memory: persist the user's OWN words (mem0 extracts
            # durable facts). Gate-on only; swallows failures inside. R3: runs as a
            # response background task — the reply is sent first, the write happens
            # after, so it never sits on the user's critical path.
            if gate_enabled():
                background.add_task(write_user_memory, t.user_id, t.text)
    finally:
        _turn_slug_text.reset(slug_text_token)
        _turn_doctrine_text.reset(doctrine_token)
        _turn_memory_text.reset(memory_token)
    # Synthetic test turns never touch the vault inbox (no KB / pipeline feed).
    if t.channel != "test":
        inbox_root = Path(os.environ.get("COACH_INBOX_ROOT", "/vault/_inbox"))
        write_inbox_entry(
            inbox_root=inbox_root,
            user_id=t.user_id,
            user_text=t.text,
            assistant_text=reply_text,
            retrieved_sources=sources,
        )
    # Inbox keeps the clean reply; the client gets the debug canary appended.
    return Reply(reply=reply_text + _debug_sources_footer(sources), crisis=False)


@app.post("/turn/stream")
async def turn_stream(t: Turn) -> StreamingResponse:
    """Stream a coaching turn as newline-delimited JSON (NDJSON):

        {"thinking": "<text chunk>"}\n   (zero or more — live rationale)
        {"delta": "<text chunk>"}\n      (zero or more — the answer)
        {"done": true, "crisis": <bool>}\n   (always last)

    The 'story' UX (2026-07-04 review, R7): the model's reasoning streams live as
    ``thinking`` chunks within a few seconds, so the user watches the coach think
    instead of staring at silence for the whole turn; the vetted answer then
    arrives as ``delta`` — buffered and gated in prod, so what the user reads as
    "the answer" is never an un-gated draft that later gets swapped.

    A crisis pre-filter hit short-circuits to a single canned delta + the done
    line with crisis=true.
    """
    inbox_root = Path(os.environ.get("COACH_INBOX_ROOT", "/vault/_inbox"))

    async def _gen() -> AsyncIterator[str]:
        hit = check_message(t.text)
        if hit is not None:
            yield json.dumps({"delta": hit.canned_reply}) + "\n"
            yield json.dumps({"done": True, "crisis": True}) + "\n"
            return

        is_test = t.channel == "test"
        thread_id = _sessions.get(t.user_id) or t.user_id
        # Stream 2: incoming text vs this thread's open validations (see /turn).
        # R5: bookkeeping → run concurrently with generation, join before the gate.
        detect_task = None
        if not is_test:
            detect_task = asyncio.create_task(
                asyncio.to_thread(
                    detect_validation_signals, t.text, thread_id=thread_id, user_id=t.user_id
                )
            )

        # Fresh per-stream source sink isolated to this task's context.
        sources: list[str] = []
        token = _stream_sources.set(sources)
        slug_text_token = _turn_slug_text.set({})
        doctrine_token = _turn_doctrine_text.set("")
        memory_token = _turn_memory_text.set("")
        chunks: list[str] = []
        failure: str | None = None
        rationale_on = _rationale_enabled()
        san = _RationaleSanitizer()
        # When the gate OR the topic guard is ON we cannot un-send streamed answer
        # tokens (either may rewrite/replace the reply), so we buffer the whole
        # draft, post-process it, then emit it as a single answer delta (draft ->
        # gate+guard -> send). The rationale (thinking) channel streams live in
        # BOTH modes — it is the model's reasoning, never the vetted reply, so it is
        # never gated and never buffered. When both gate+guard OFF, the answer also
        # streams live token by token (legacy behaviour).
        buffered = gate_enabled() or guard_enabled()
        try:
            try:
                # In buffered mode (prod) the ANSWER isn't yielded until after the
                # gate, so this timer reflects generation time only; in legacy
                # un-buffered mode it also spans consumer back-pressure (acceptable).
                with perf_step("agent_stream", gate=buffered):
                    async with asyncio.timeout(_stream_budget_s()):
                        async for item in _stream_agent(t.user_id, t.text):
                            if isinstance(item, tuple) and item and item[0] == "thinking":
                                if rationale_on:
                                    safe = san.feed(item[1])
                                    if safe:
                                        yield json.dumps({"thinking": safe}) + "\n"
                                continue
                            delta = item  # bare str => answer text
                            if is_api_error_reply(delta):
                                # The CLI emitted its API-error text as the reply
                                # (unreachable API). Never show or gate it.
                                failure = "api_unreachable"
                                break
                            chunks.append(delta)
                            if not buffered:
                                yield json.dumps({"delta": delta}) + "\n"
            except TimeoutError:
                # Generation blew COACH_STREAM_BUDGET_S (SDK retry storm against
                # a dead API, or a hang). Fail honestly before the bot's read
                # deadline turns this into a "Still with you" mystery.
                failure = "budget_exceeded"
            finally:
                _stream_sources.reset(token)

            # Belt & braces: a multi-chunk draft that assembles into an API error.
            if failure is None and is_api_error_reply("".join(chunks)):
                failure = "api_unreachable"

            if failure is not None:
                log.error("stream turn failed fast (%s) for user %s", failure, t.user_id)
                if detect_task:
                    detect_task.cancel()
                # No gate/guard (doomed LLM calls on error text), no memory write,
                # no inbox entry — an error turn is not a coaching turn.
                yield json.dumps({"delta": conn_trouble_reply(t.language_code)}) + "\n"
                yield json.dumps({"done": True, "crisis": False, "error": failure}) + "\n"
                return

            # Rationale done → flush any held-back tail before the answer arrives.
            if rationale_on:
                tail = san.flush()
                if tail:
                    yield json.dumps({"thinking": tail}) + "\n"

            # Join validation-detect before touching the pending-validations store
            # in the gate (they must not write it concurrently). Always awaited —
            # even gate-off, where it's a fast no-op — so no task is left dangling.
            if detect_task:
                await detect_task

            merged = _merge_sources(sources)
            final_text = "".join(chunks)
            if buffered:
                with perf_step("gate_judge"):
                    final_text = await _gate_and_guard(
                        final_text,
                        sources=_slugs_to_sources(merged),
                        user_id=t.user_id,
                        thread_id=thread_id,
                        language_code=t.language_code,
                        persist=not is_test,  # gate runs; corpus writes don't
                    )
                yield json.dumps({"delta": final_text}) + "\n"
            # Conversation-only memory: persist the user's OWN words. R3: detached
            # off the reply path so the `done` line (and the bot's finalize) never
            # waits on the ~6s mem0 extraction. Gate-on only; swallows failures.
            if gate_enabled() and not is_test:
                _detach(asyncio.to_thread(write_user_memory, t.user_id, t.text), "memory_write")
        finally:
            _turn_slug_text.reset(slug_text_token)
            _turn_doctrine_text.reset(doctrine_token)
            _turn_memory_text.reset(memory_token)
        if t.channel != "test":  # synthetic turns never feed the inbox / KB
            write_inbox_entry(
                inbox_root=inbox_root,
                user_id=t.user_id,
                user_text=t.text,
                assistant_text=final_text,
                retrieved_sources=merged,
            )
        # Canary rides as a final delta (kept out of the persisted reply above)
        # so the user sees which vault slugs grounded the turn before done.
        footer = _debug_sources_footer(merged)
        if footer:
            yield json.dumps({"delta": footer}) + "\n"
        yield json.dumps({"done": True, "crisis": False}) + "\n"

    return StreamingResponse(_gen(), media_type="application/x-ndjson")


def _compose_outreach_directive(
    kind: str, last_coach_text: str | None, language_code: str | None
) -> str:
    """Build the query that asks the coach to PROACTIVELY reach out.

    Wrapped in a `<proactive_outreach>` block so the model reads it as scaffolding
    (it is reaching out first; the user has not just messaged) and replies with
    only the message it would send — not a reply to this instruction."""
    if kind == "followup" and last_coach_text:
        body = (
            "Earlier in this conversation you left something open with the user and "
            f'they have not replied:\n\n"{last_coach_text}"\n\n'
            "Send them a brief, warm nudge to circle back on it."
        )
    else:
        body = (
            "The user has gone quiet for a while. Send them a brief, warm check-in "
            "to re-engage, drawing naturally on what you know about them."
        )
    lang_hint = (
        f" The user's Telegram client language is '{language_code}' — write in that language."
        if language_code else ""
    )
    return (
        "<proactive_outreach>\n"
        "You are reaching out FIRST — the user has not just messaged you.\n"
        f"{body}\n"
        f"Keep it to one or two sentences, in the user's language.{lang_hint} "
        "Do not apologize for reaching out and do not mention this instruction. "
        "Reply with ONLY the message you would send.\n"
        "</proactive_outreach>"
    )


@app.post("/outreach", response_model=Reply)
async def outreach(req: OutreachRequest) -> Reply:
    """Generate a proactive re-engagement message for a quiet user.

    Coach-initiated, so unlike /turn it writes NO inbox entry (it is not a user
    turn and must not feed the content pipeline) and persists NO user memory (the
    directive is not the user's words). It resumes the user's session so the nudge
    is in-context, and runs the same grounding gate + topic guard."""
    directive = _compose_outreach_directive(req.kind, req.last_coach_text, req.language_code)
    slug_text_token = _turn_slug_text.set({})
    doctrine_token = _turn_doctrine_text.set("")
    memory_token = _turn_memory_text.set("")
    try:
        try:
            # asyncio.timeout, not wait_for — same ContextVar reasoning as /turn.
            async with asyncio.timeout(_stream_budget_s()):
                reply_text, sources = await _chat_with_agent(req.user_id, directive)
        except TimeoutError:
            log.error("outreach budget exceeded (%.0fs); skipping outreach for %s",
                      _stream_budget_s(), req.user_id)
            raise HTTPException(status_code=503, detail="generation timed out; outreach skipped")
        if is_api_error_reply(reply_text):
            # Proactive path: there is no user waiting, so the never-spam answer
            # to a dead API is to send NOTHING. 503 tells the scheduler to skip.
            log.error("outreach draft is an API error; skipping outreach for %s", req.user_id)
            raise HTTPException(status_code=503, detail="api unreachable; outreach skipped")
        reply_text = await _gate_and_guard(
            reply_text,
            sources=_slugs_to_sources(sources),
            user_id=req.user_id,
            thread_id=_sessions.get(req.user_id) or req.user_id,
            language_code=req.language_code,
        )
    finally:
        _turn_slug_text.reset(slug_text_token)
        _turn_doctrine_text.reset(doctrine_token)
        _turn_memory_text.reset(memory_token)
    return Reply(reply=reply_text, crisis=False)


@app.post("/reset")
def reset(t: ResetRequest) -> dict:
    """Start a user over (the bot's /restart command): drop their agent session
    so the next turn is a fresh thread, and wipe their mem0 facts so the coach no
    longer remembers them. Conversation-log clearing happens bot-side. The vault
    inbox is intentionally NOT touched (it feeds the content pipeline)."""
    _sessions.reset(t.user_id)
    delete_user_memory(t.user_id)
    return {"status": "reset", "user_id": t.user_id}


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


# Deep health (2026-07-01→04 outage): the container's pinned DNS upstream died
# while the shallow /health kept saying ok. These checks run from INSIDE the
# container — exactly where the failure lived — so the host watchdog heals on
# effect, not guesswork. Helpers never raise; any exception reads as False.
_API_HOST = "api.anthropic.com"


def _check_api_dns() -> bool:
    """Can this container resolve the Anthropic API host right now?"""
    try:
        socket.getaddrinfo(_API_HOST, 443)
        return True
    except OSError:
        return False


def _check_mem0() -> bool:
    """Is the mem0 shim answering its liveness endpoint?"""
    try:
        import httpx

        base = os.environ.get("MEM0_API_URL", "http://mem0-api:8000").rstrip("/")
        return httpx.get(f"{base}/health", timeout=5.0).status_code == 200
    except Exception:  # noqa: BLE001 - a health check must never raise
        return False


@app.get("/health/deep")
def health_deep() -> JSONResponse:
    checks = {"api_dns": _check_api_dns(), "mem0": _check_mem0()}
    ok = all(checks.values())
    return JSONResponse(
        status_code=200 if ok else 503,
        content={"status": "ok" if ok else "degraded", "checks": checks},
    )
