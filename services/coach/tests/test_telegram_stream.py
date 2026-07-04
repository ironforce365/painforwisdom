"""on_message streams the coaching turn: sends a placeholder message, then
edits it progressively as deltas arrive, ending with the full text.

Edits are throttled to respect Telegram rate limits, but the FINAL edit always
carries the complete concatenated reply.
"""
from __future__ import annotations
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import telegram_bot.bot as bot


def _extract_on_message(app) -> "callable":
    """Pull the on_message callback out of the built Application's handlers."""
    from telegram.ext import MessageHandler

    for group in app.handlers.values():
        for h in group:
            if isinstance(h, MessageHandler):
                return h.callback
    raise AssertionError("on_message handler not found")


@pytest.fixture
def built(monkeypatch, tmp_path):
    monkeypatch.setenv("TELEGRAM_COACH_BOT_TOKEN", "123:abc")
    access = tmp_path / "access.json"
    access.write_text(
        json.dumps({"version": 1, "policy": "allowlist", "allowed_user_ids": [99]})
    )
    monkeypatch.setenv("COACH_ACCESS_JSON", str(access))
    welcomed = tmp_path / "welcomed.json"
    # Pre-mark user 99 as already welcomed so the first-contact welcome message
    # doesn't add an extra reply_text these streaming tests don't expect.
    welcomed.write_text(json.dumps({"version": 1, "welcomed_user_ids": [99]}))
    monkeypatch.setenv("COACH_WELCOME_JSON", str(welcomed))
    monkeypatch.setenv("COACH_QUOTA_JSON", str(tmp_path / "quota.json"))
    monkeypatch.setenv("COACH_CONVO_LOG_DIR", str(tmp_path / "conversations"))
    monkeypatch.setenv("COACH_OUTREACH_JSON", str(tmp_path / "outreach.json"))

    # Replace CoachClient with a stub whose stream_turn we can drive per-test.
    stub_coach = MagicMock()
    monkeypatch.setattr(bot, "CoachClient", lambda *a, **k: stub_coach)

    app = bot._build_app()
    on_message = _extract_on_message(app)
    return SimpleNamespace(on_message=on_message, coach=stub_coach)


def _make_update(text="hello", user_id=99):
    placeholder = SimpleNamespace(message_id=555, chat_id=user_id)
    msg = MagicMock()
    msg.text = text
    msg.voice = None
    msg.message_id = 1
    msg.chat_id = user_id
    msg.reply_text = AsyncMock(return_value=placeholder)
    user = SimpleNamespace(id=user_id, username="u", full_name="U")
    update = SimpleNamespace(effective_user=user, effective_message=msg)
    return update, msg, placeholder


def _make_ctx():
    ctx = SimpleNamespace()
    ctx.bot = SimpleNamespace(
        edit_message_text=AsyncMock(),
        send_message=AsyncMock(),
        send_chat_action=AsyncMock(),
    )
    return ctx


@pytest.mark.asyncio
async def test_streams_placeholder_then_progressive_edits(built):
    built.coach.stream_turn.return_value = iter(
        ["Run ", "in the ", "rain. ", "Lean ", "into it."]
    )
    update, msg, placeholder = _make_update()
    ctx = _make_ctx()

    await built.on_message(update, ctx)

    # A placeholder message was sent first.
    assert msg.reply_text.await_count == 1

    # Progressive edits happened against the placeholder message.
    assert ctx.bot.edit_message_text.await_count >= 1

    # The final edit carries the full concatenated reply.
    final_call = ctx.bot.edit_message_text.await_args_list[-1]
    final_text = final_call.kwargs.get("text") or final_call.args[0]
    assert final_text == "Run in the rain. Lean into it."


@pytest.mark.asyncio
async def test_stream_failure_falls_back_to_error_reply(built, monkeypatch):
    import httpx

    def _boom(*a, **k):
        raise httpx.ConnectError("refused")

    built.coach.stream_turn.side_effect = _boom
    update, msg, placeholder = _make_update()
    ctx = _make_ctx()

    await built.on_message(update, ctx)

    # The user is told the coach is down (DOWN_REPLY for a connect failure).
    from telegram_bot.coach_client import DOWN_REPLY

    # Fallback text shows up either as a fresh reply or as an edit of the placeholder.
    shown = []
    for c in msg.reply_text.await_args_list:
        shown.append((c.kwargs.get("text") or (c.args[0] if c.args else "")))
    for c in ctx.bot.edit_message_text.await_args_list:
        shown.append((c.kwargs.get("text") or (c.args[0] if c.args else "")))
    assert DOWN_REPLY in shown
