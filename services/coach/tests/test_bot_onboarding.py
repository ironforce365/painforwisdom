"""on_message wiring for the pilot: welcome-once, daily quota, conversation log.

Order of gates for an allowlisted user:
  1. welcome (once per user, in their language) — /start only welcomes
  2. daily quota — blocked users get a localized notice, no coaching turn
  3. log inbound + run the coaching turn + log the coach reply
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import telegram_bot.bot as bot
from telegram_bot.conversation_log import ConversationLog


@pytest.fixture
def built(monkeypatch, tmp_path):
    monkeypatch.setenv("TELEGRAM_COACH_BOT_TOKEN", "123:abc")
    access = tmp_path / "access.json"
    access.write_text(
        json.dumps({"version": 1, "policy": "allowlist", "allowed_user_ids": [99]})
    )
    monkeypatch.setenv("COACH_ACCESS_JSON", str(access))
    monkeypatch.setenv("COACH_WELCOME_JSON", str(tmp_path / "welcomed.json"))
    monkeypatch.setenv("COACH_QUOTA_JSON", str(tmp_path / "quota.json"))
    monkeypatch.setenv("COACH_QUOTA_LIMIT", "2")
    monkeypatch.setenv("COACH_QUOTA_TZ", "UTC")
    monkeypatch.setenv("COACH_CONVO_LOG_DIR", str(tmp_path / "conversations"))

    stub_coach = MagicMock()
    monkeypatch.setattr(bot, "CoachClient", lambda *a, **k: stub_coach)

    app = bot._build_app()
    on_message = None
    from telegram.ext import MessageHandler
    for group in app.handlers.values():
        for h in group:
            if isinstance(h, MessageHandler):
                on_message = h.callback
    return SimpleNamespace(on_message=on_message, coach=stub_coach, tmp=tmp_path)


def _make_update(text="hello", user_id=99, lang="es"):
    placeholder = SimpleNamespace(message_id=555, chat_id=user_id)
    msg = MagicMock()
    msg.text = text
    msg.voice = None
    msg.message_id = 1
    msg.chat_id = user_id
    msg.reply_text = AsyncMock(return_value=placeholder)
    user = SimpleNamespace(id=user_id, username="u", full_name="Ana", language_code=lang)
    update = SimpleNamespace(effective_user=user, effective_message=msg)
    return update, msg


def _make_ctx():
    ctx = SimpleNamespace()
    ctx.bot = SimpleNamespace(
        edit_message_text=AsyncMock(),
        send_message=AsyncMock(),
        send_chat_action=AsyncMock(),
    )
    return ctx


def _reply_texts(msg):
    return [
        (c.kwargs.get("text") or (c.args[0] if c.args else ""))
        for c in msg.reply_text.await_args_list
    ]


@pytest.mark.asyncio
async def test_new_user_is_welcomed_in_their_language_then_coached(built):
    built.coach.stream_turn.return_value = iter(["Vamos."])
    update, msg = _make_update(text="quiero correr", lang="es")
    await built.on_message(update, _make_ctx())

    texts = _reply_texts(msg)
    # Welcome (Spanish) was sent...
    assert any("Hola" in t and "metas" in t for t in texts)
    # ...and the coaching turn still ran for the actual question.
    assert built.coach.stream_turn.called


@pytest.mark.asyncio
async def test_returning_user_is_not_welcomed_again(built):
    built.coach.stream_turn.return_value = iter(["ok"])
    # First contact welcomes.
    await built.on_message(*_with_ctx(_make_update(text="hi")))
    # Second contact must NOT welcome again.
    update2, msg2 = _make_update(text="more")
    built.coach.stream_turn.return_value = iter(["ok2"])
    await built.on_message(update2, _make_ctx())
    assert not any("Hola" in t for t in _reply_texts(msg2))


@pytest.mark.asyncio
async def test_start_command_only_welcomes(built):
    update, msg = _make_update(text="/start")
    await built.on_message(update, _make_ctx())
    assert any("Hola" in t for t in _reply_texts(msg))
    assert not built.coach.stream_turn.called  # nothing to coach


@pytest.mark.asyncio
async def test_quota_block_after_limit(built):
    # limit=2 (from fixture). Mark user already welcomed so welcome doesn't interfere.
    update0, _ = _make_update(text="x")
    built.coach.stream_turn.return_value = iter(["a"])
    await built.on_message(update0, _make_ctx())  # msg 1 (welcome + turn)
    built.coach.stream_turn.return_value = iter(["b"])
    await built.on_message(*_with_ctx(_make_update(text="y")))  # msg 2
    built.coach.stream_turn.reset_mock()

    update3, msg3 = _make_update(text="z", lang="es")
    await built.on_message(update3, _make_ctx())  # msg 3 → blocked
    texts = _reply_texts(msg3)
    assert any("límite diario" in t and "2" in t for t in texts)
    assert not built.coach.stream_turn.called


@pytest.mark.asyncio
async def test_conversation_is_logged(built):
    built.coach.stream_turn.return_value = iter(["Hola ", "Ana."])
    update, msg = _make_update(text="quiero correr", lang="es")
    await built.on_message(update, _make_ctx())

    log = ConversationLog(built.tmp / "conversations")
    msgs = log.read_conversation(99)
    roles = [m["role"] for m in msgs]
    assert "user" in roles and "coach" in roles
    user_rec = next(m for m in msgs if m["role"] == "user")
    assert user_rec["text"] == "quiero correr"
    assert user_rec["name"] == "Ana"
    coach_rec = next(m for m in msgs if m["role"] == "coach")
    assert coach_rec["text"] == "Hola Ana."


@pytest.mark.asyncio
async def test_restart_wipes_history_resets_agent_and_regreets(built):
    # Build up some state: a welcome + a logged coaching turn.
    built.coach.stream_turn.return_value = iter(["ok"])
    await built.on_message(*_with_ctx(_make_update(text="quiero correr")))
    assert ConversationLog(built.tmp / "conversations").read_conversation(99)  # has history
    built.coach.stream_turn.reset_mock()

    # /restart starts the user over.
    update, msg = _make_update(text="/restart", lang="es")
    await built.on_message(update, _make_ctx())

    # Agent session + mem0 reset was requested for this user.
    built.coach.reset.assert_called_once_with("99")
    # Conversation history wiped.
    assert ConversationLog(built.tmp / "conversations").read_conversation(99) == []
    # Re-greeted in their language...
    assert any("Hola" in t and "metas" in t for t in _reply_texts(msg))
    # ...and /restart itself is not a coaching turn.
    assert not built.coach.stream_turn.called


@pytest.mark.asyncio
async def test_restart_resets_quota(built):
    # limit=2 (fixture). Exhaust it, /restart, then a fresh message is allowed.
    built.coach.stream_turn.return_value = iter(["a"])
    await built.on_message(*_with_ctx(_make_update(text="1")))  # quota 1
    await built.on_message(*_with_ctx(_make_update(text="2")))  # quota 2 (now at limit)
    await built.on_message(*_with_ctx(_make_update(text="/restart")))  # resets quota
    built.coach.stream_turn.reset_mock()
    built.coach.stream_turn.return_value = iter(["b"])

    # A normal message after /restart is allowed again (quota was reset).
    update, msg = _make_update(text="otra vez")
    await built.on_message(update, _make_ctx())
    assert built.coach.stream_turn.called
    assert not any("límite diario" in t for t in _reply_texts(msg))


def _with_ctx(update_msg):
    """Helper: pair an (update, msg) with a fresh ctx for await built.on_message(*...)."""
    update, _ = update_msg
    return update, _make_ctx()
