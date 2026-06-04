"""thinking-ack: the bot shows a TYPING chat action during the pre-token gap of
a streamed coaching turn, then lets the progressively-edited message take over.

Two layers:
- `_keep_typing` must re-send TYPING (the indicator expires ~5s) and cancel cleanly.
- `_stream_reply` (driven via on_message) must start typing, send it at least
  once before the first delta, and always stop it — on success and on error.
"""
from __future__ import annotations
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram.constants import ChatAction

import telegram_bot.bot as bot


@pytest.mark.asyncio
async def test_keep_typing_sends_typing_and_cancels_cleanly():
    fake_bot = MagicMock()
    fake_bot.send_chat_action = AsyncMock()

    task = asyncio.create_task(bot._keep_typing(fake_bot, chat_id=123, interval=0.01))
    await asyncio.sleep(0.05)  # let it loop a couple times
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert fake_bot.send_chat_action.await_count >= 1
    _, kwargs = fake_bot.send_chat_action.await_args
    assert kwargs["chat_id"] == 123
    assert kwargs["action"] == ChatAction.TYPING


def _extract_on_message(app):
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
    stub_coach = MagicMock()
    monkeypatch.setattr(bot, "CoachClient", lambda *a, **k: stub_coach)
    app = bot._build_app()
    return SimpleNamespace(on_message=_extract_on_message(app), coach=stub_coach)


def _make_update(text="hello", user_id=99):
    placeholder = SimpleNamespace(message_id=555, chat_id=user_id)
    msg = MagicMock()
    msg.text = text
    msg.voice = None
    msg.message_id = 1
    msg.chat_id = user_id
    msg.reply_text = AsyncMock(return_value=placeholder)
    user = SimpleNamespace(id=user_id, username="u", full_name="U")
    return SimpleNamespace(effective_user=user, effective_message=msg), msg


def _make_ctx():
    ctx = SimpleNamespace()
    ctx.bot = SimpleNamespace(
        edit_message_text=AsyncMock(),
        send_message=AsyncMock(),
        send_chat_action=AsyncMock(),
    )
    return ctx


@pytest.mark.asyncio
async def test_stream_reply_sends_typing_then_stops(built):
    built.coach.stream_turn.return_value = iter(["Run ", "in the rain."])
    update, msg = _make_update()
    ctx = _make_ctx()

    await built.on_message(update, ctx)

    # Typing was shown during the turn...
    assert ctx.bot.send_chat_action.await_count >= 1
    assert ctx.bot.send_chat_action.await_args.kwargs["action"] == ChatAction.TYPING

    # ...and stopped once the turn finished (no further typing afterwards).
    before = ctx.bot.send_chat_action.await_count
    await asyncio.sleep(0.05)
    assert ctx.bot.send_chat_action.await_count == before


@pytest.mark.asyncio
async def test_stream_reply_stops_typing_on_error(built):
    import httpx

    def _boom(*a, **k):
        raise httpx.ConnectError("refused")

    built.coach.stream_turn.side_effect = _boom
    update, msg = _make_update()
    ctx = _make_ctx()

    await built.on_message(update, ctx)

    # Keep-alive must not outlive a failed turn.
    before = ctx.bot.send_chat_action.await_count
    await asyncio.sleep(0.05)
    assert ctx.bot.send_chat_action.await_count == before
