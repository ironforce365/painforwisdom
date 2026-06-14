"""FastAPI service: POST /turn (user_id, text) → coach reply.

Wires together:
- crisis pre-filter
- claude-agent-sdk client with 3 MCP servers (vault_rag, user_memory, mem0)
- post-turn inbox hook
"""
from __future__ import annotations
import contextvars
import json
import os
from pathlib import Path
from typing import AsyncIterator, Tuple

from claude_agent_sdk import TextBlock, ToolResultBlock, ToolUseBlock
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from agent.hooks import write_inbox_entry
from agent.memory import read_user_memory, write_user_memory
from agent.prompts import compose_system_prompt
from agent.retrieval import retrieve_doctrine_for_turn, retrieve_for_turn_rich
from agent.session_map import SessionMap
from crisis.filter import check_message

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


# Namespaced MCP tool name. The claude-agent-sdk MCP tool naming convention is
# `mcp__<server_key>__<tool>` where `<server_key>` is the key in the
# `mcp_servers` dict passed to ClaudeAgentOptions (NOT the FastMCP constructor
# name).
SEARCH_VAULT_TOOL = "mcp__vault_rag__search_vault"

app = FastAPI(title="painforwisdom-coach")
_sessions = SessionMap()

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


class Reply(BaseModel):
    reply: str
    crisis: bool


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


def _compose_turn_prompt(user_id: str, text: str) -> Tuple[str, list[str]]:
    """Build the agent query for a turn, injecting deterministically pre-retrieved
    vault context ahead of the user's message.

    Returns (query, pre_retrieved_slugs). The slugs seed the inbox source list so
    a turn is recorded as grounded even when the model never calls search_vault.
    The context block is placed before the user's text so the model reads its
    grounding first; it is omitted entirely when retrieval found nothing.
    """
    parts = [f"<user_id>{user_id}</user_id>"]
    if gate_enabled():
        # Doctrine + memory world: ground teaching on distilled doctrine (D1) and
        # facts about the user ONLY on conversation-derived memory (M1). The raw
        # vault is never retrieved at coach-time here — it is only distillation
        # input. Slugs (doctrine principles) still feed the inbox + debug canary.
        doctrine_block, slugs, doctrine_text = retrieve_doctrine_for_turn(text)
        memory_block, memory_text = read_user_memory(user_id, text)
        _turn_doctrine_text.set(doctrine_text)
        _turn_memory_text.set(memory_text)
        if doctrine_block:
            parts.append(doctrine_block)
        if memory_block:
            parts.append(memory_block)
        parts.append(text)
        return "\n\n".join(parts), slugs
    # Legacy (flag OFF): raw vault context, byte-identical to before.
    context_block, slugs, slug_text = retrieve_for_turn_rich(text)
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


def _build_agent_options(user_id: str):
    """Construct ClaudeAgentOptions for a turn (resume + MCP servers + allowlist).

    Shared by `_chat_with_agent` and `_stream_agent` so both run with identical
    session-resume and tooling configuration.
    """
    from claude_agent_sdk import ClaudeAgentOptions
    from claude_agent_sdk.types import McpStdioServerConfig

    # Resume an existing CLI session only if we have a real id from a prior turn.
    # Passing a fabricated id makes `claude --resume <id>` fail ("No conversation
    # found"); first turn must run with resume=None so the SDK creates the session.
    session_id = _sessions.get(user_id)
    # When the gate is ON, point the `search_vault` tool at the DOCTRINE index so a
    # mid-turn dig-deeper returns de-personalised principles, never raw biography.
    # Without this, the model can call search_vault, hit the raw vault, and narrate
    # the author's personal history (the "four months of recovery" leak) — which the
    # claim gate won't catch because it's phrased about the author, not the user.
    vault_env = dict(os.environ)
    if gate_enabled():
        vault_env["COACH_INDEX_STORAGE_DIR"] = os.environ.get(
            "COACH_DOCTRINE_INDEX_DIR", "/data/doctrine_index"
        )
    return ClaudeAgentOptions(
        # Claim contract appended ONLY when the gate is on; flag off => prompt
        # byte-identical to before, no [[claim]] tags can ever leak.
        system_prompt=compose_system_prompt(claims=gate_enabled()),
        resume=session_id,
        # Headless service: auto-approve the coach's own MCP tools (server-level
        # patterns cover every tool each server exposes). Without this the SDK's
        # permission gate denies the tool calls and the agent can't reach the
        # vault / memory. Built-in tools stay un-allowlisted → denied.
        allowed_tools=["mcp__vault_rag", "mcp__user_memory", "mcp__mem0"],
        mcp_servers={
            "vault_rag": McpStdioServerConfig(
                command="python", args=["-m", "vault_rag.mcp_server"], env=vault_env,
            ),
            "user_memory": McpStdioServerConfig(
                command="python", args=["-m", "user_memory.mcp_server"], env=dict(os.environ),
            ),
            "mem0": McpStdioServerConfig(
                command="python", args=["-m", "mem0_mcp.mcp_server"], env=dict(os.environ),
            ),
        },
    )


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
    query, pre_slugs = _compose_turn_prompt(user_id, text)

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


async def _stream_agent(user_id: str, text: str) -> AsyncIterator[str]:
    """Streaming counterpart of `_chat_with_agent`: spin up ClaudeSDKClient and
    yield assistant text chunks as they arrive.

    Source slugs collected from search_vault tool results are appended into the
    `_stream_sources` ContextVar list so the endpoint can write them to the
    inbox once the stream completes.

    Monkeypatched in tests.
    """
    from claude_agent_sdk import ClaudeSDKClient

    options = _build_agent_options(user_id)
    captured: dict[str, str] = {}
    sources: list[str] = _stream_sources.get()
    query, pre_slugs = _compose_turn_prompt(user_id, text)
    # Seed the inbox source sink with the deterministic pre-retrieval so the turn
    # is recorded as grounded even if the model adds no search_vault calls; the
    # endpoint dedupes before writing.
    sources.extend(pre_slugs)

    async with ClaudeSDKClient(options=options) as client:
        await client.query(query)
        async for chunk in _walk_messages(
            _sniff_session(client.receive_response(), captured), sources
        ):
            yield chunk
    if captured.get("sid"):
        _sessions.set(user_id, captured["sid"])


@app.post("/turn", response_model=Reply)
async def turn(t: Turn) -> Reply:
    hit = check_message(t.text)
    if hit is not None:
        return Reply(reply=hit.canned_reply, crisis=True)

    # Stream 2: match the incoming text against this thread's open validations
    # (questions the coach previously asked / reads it stated). Confirmations and
    # corrections are logged to the corpus and close their items. No-op when the
    # gate flag is off; failures are swallowed inside.
    detect_validation_signals(
        t.text,
        thread_id=_sessions.get(t.user_id) or t.user_id,
        user_id=t.user_id,
    )

    slug_text_token = _turn_slug_text.set({})
    doctrine_token = _turn_doctrine_text.set("")
    memory_token = _turn_memory_text.set("")
    try:
        reply_text, sources = await _chat_with_agent(t.user_id, t.text)
        # Gate before persisting: when ON the gated text IS the reply (demotions
        # change content), so inbox and client must both see it. Footer stays last.
        reply_text = maybe_gate(
            reply_text,
            sources=_slugs_to_sources(sources),
            user_id=t.user_id,
            thread_id=_sessions.get(t.user_id) or t.user_id,
        )
        # Conversation-only memory: persist the user's OWN words (mem0 extracts
        # durable facts). Gate-on only; swallows failures inside.
        if gate_enabled():
            write_user_memory(t.user_id, t.text)
    finally:
        _turn_slug_text.reset(slug_text_token)
        _turn_doctrine_text.reset(doctrine_token)
        _turn_memory_text.reset(memory_token)
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

        {"delta": "<text chunk>"}\n   (zero or more)
        {"done": true, "crisis": <bool>}\n   (always last)

    A crisis pre-filter hit short-circuits to a single canned delta + the done
    line with crisis=true. On the happy path, assistant text streams as it
    generates and the full concatenated reply + collected sources are written to
    the inbox once the stream completes.
    """
    inbox_root = Path(os.environ.get("COACH_INBOX_ROOT", "/vault/_inbox"))

    async def _gen() -> AsyncIterator[str]:
        hit = check_message(t.text)
        if hit is not None:
            yield json.dumps({"delta": hit.canned_reply}) + "\n"
            yield json.dumps({"done": True, "crisis": True}) + "\n"
            return

        # Stream 2: incoming text vs this thread's open validations (see /turn).
        detect_validation_signals(
            t.text,
            thread_id=_sessions.get(t.user_id) or t.user_id,
            user_id=t.user_id,
        )

        # Fresh per-stream source sink isolated to this task's context.
        sources: list[str] = []
        token = _stream_sources.set(sources)
        slug_text_token = _turn_slug_text.set({})
        doctrine_token = _turn_doctrine_text.set("")
        memory_token = _turn_memory_text.set("")
        chunks: list[str] = []
        # When the gate is ON we cannot un-send streamed tokens, so we buffer
        # the whole draft, gate it, then emit it as a single delta (draft ->
        # gate -> send). When OFF, behaviour is identical: live token streaming.
        gated = gate_enabled()
        try:
            async for delta in _stream_agent(t.user_id, t.text):
                chunks.append(delta)
                if not gated:
                    yield json.dumps({"delta": delta}) + "\n"
        finally:
            _stream_sources.reset(token)
        merged = _merge_sources(sources)
        final_text = "".join(chunks)
        try:
            if gated:
                final_text = maybe_gate(
                    final_text,
                    sources=_slugs_to_sources(merged),
                    user_id=t.user_id,
                    thread_id=_sessions.get(t.user_id) or t.user_id,
                )
                yield json.dumps({"delta": final_text}) + "\n"
                # Conversation-only memory: persist the user's OWN words.
                write_user_memory(t.user_id, t.text)
        finally:
            _turn_slug_text.reset(slug_text_token)
            _turn_doctrine_text.reset(doctrine_token)
            _turn_memory_text.reset(memory_token)
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


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}
