"""Telegram polling bot. Allowlist → (voice|text) → coach service → reply.

Unknown users trigger an admin approval flow: the admin (chat_id from
TELEGRAM_COACH_ALERT_CHAT_ID) gets a DM with the requester's info and inline
Approve/Deny buttons. Approve mutates access.json atomically and the runtime
allowlist picks it up immediately — no restart required."""
from __future__ import annotations
import asyncio
import logging
import os
import time
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application, CallbackQueryHandler, ContextTypes, MessageHandler, filters,
)

from telegram_bot.allowlist import Allowlist
from telegram_bot.coach_client import CoachClient, coach_error_reply
from telegram_bot.voice import transcribe_voice

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("coach.telegram")

# In-memory pending approval requests, keyed by requester user_id. Lost on
# bot restart (acceptable: requester just re-DMs and admin gets a new ping).
_pending: dict[int, dict] = {}

# Telegram caps edit_message_text frequency; editing on every tiny delta would
# trip 429s on a long reply. Throttle: edit at most once per interval OR once a
# meaningful amount of new text has accumulated, whichever comes first. The
# final edit (after the stream ends) always flushes the complete text.
_EDIT_MIN_INTERVAL_S = 1.0
_EDIT_MIN_CHARS = 40
_PLACEHOLDER_TEXT = "…"


async def _stream_reply(coach, user, msg, ctx, text: str) -> None:
    """Stream a coaching turn into Telegram: send a placeholder message, then
    edit it progressively as deltas arrive (throttled), and flush the full text
    on completion. On failure, fall back to coach_error_reply.

    The blocking stream_turn() iterator is drained one chunk at a time off the
    event loop via asyncio.to_thread so the bot stays responsive and no single
    request blocks the loop for the full ~56s turn.
    """
    placeholder = await msg.reply_text(_PLACEHOLDER_TEXT)
    chat_id = placeholder.chat_id
    message_id = placeholder.message_id

    acc = ""
    last_edit_at = 0.0
    last_sent = ""
    sentinel = object()

    async def _edit(body: str) -> None:
        nonlocal last_edit_at, last_sent
        if not body or body == last_sent:
            return
        await ctx.bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=body)
        last_edit_at = time.monotonic()
        last_sent = body

    try:
        it = await asyncio.to_thread(coach.stream_turn, str(user.id), text)
        while True:
            chunk = await asyncio.to_thread(next, it, sentinel)
            if chunk is sentinel:
                break
            acc += chunk
            now = time.monotonic()
            if (now - last_edit_at) >= _EDIT_MIN_INTERVAL_S or (
                len(acc) - len(last_sent)
            ) >= _EDIT_MIN_CHARS:
                await _edit(acc)
        # Final flush: guarantee the complete reply is shown.
        await _edit(acc)
        if not acc.strip():
            # Stream produced nothing — leave the user with something honest.
            await _edit("(no response)")
    except Exception as exc:
        # A read timeout means the agent is slow but alive; only connect /
        # protocol failures are a real outage. coach_error_reply picks the
        # honest message so a slow-but-working turn no longer reads as down.
        log.exception("coach stream failed")
        try:
            await ctx.bot.edit_message_text(
                chat_id=chat_id, message_id=message_id, text=coach_error_reply(exc)
            )
        except Exception:
            await msg.reply_text(coach_error_reply(exc))


def _build_app() -> Application:
    token = os.environ["TELEGRAM_COACH_BOT_TOKEN"]
    allowlist = Allowlist(Path(os.environ.get("COACH_ACCESS_JSON", "/app/access.json")))
    coach = CoachClient(os.environ.get("COACH_AGENT_URL", "http://coach-agent:8800"))

    async def _handle_pending(user, msg, ctx):
        admin_chat_id = os.environ.get("TELEGRAM_COACH_ALERT_CHAT_ID")
        if not admin_chat_id:
            log.warning("TELEGRAM_COACH_ALERT_CHAT_ID not set; cannot route approval request")
            await msg.reply_text("Bot not configured for new users yet. Try again later.")
            return
        if user.id in _pending:
            await msg.reply_text("Request already pending. Hold tight.")
            return
        _pending[user.id] = {
            "username": user.username,
            "full_name": user.full_name,
            "first_msg": (msg.text or "(voice)")[:200],
        }
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Approve", callback_data=f"approve:{user.id}"),
            InlineKeyboardButton("❌ Deny", callback_data=f"deny:{user.id}"),
        ]])
        summary = (
            f"New coach request:\n"
            f"  user_id: {user.id}\n"
            f"  username: @{user.username or '(none)'}\n"
            f"  name: {user.full_name}\n"
            f"  first message: {_pending[user.id]['first_msg']}"
        )
        await ctx.bot.send_message(chat_id=admin_chat_id, text=summary, reply_markup=keyboard)
        await msg.reply_text("Request sent to the coach. You'll hear back shortly.")

    async def on_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        msg = update.effective_message
        if not user or not msg:
            return
        if not allowlist.allowed(user.id):
            await _handle_pending(user, msg, ctx)
            return
        if msg.voice:
            file = await msg.voice.get_file()
            tmp = Path(f"/tmp/voice_{user.id}_{msg.message_id}.ogg")
            await file.download_to_drive(tmp)
            text = await asyncio.to_thread(transcribe_voice, tmp)
            tmp.unlink(missing_ok=True)
        else:
            text = msg.text or ""
        if not text.strip():
            return
        await _stream_reply(coach, user, msg, ctx, text)

    async def on_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        if not query or not query.data:
            return
        await query.answer()
        admin_chat_id = os.environ.get("TELEGRAM_COACH_ALERT_CHAT_ID")
        if str(query.from_user.id) != str(admin_chat_id):
            log.warning("non-admin tapped approval button: %s", query.from_user.id)
            return
        try:
            action, target_id_str = query.data.split(":", 1)
            target_id = int(target_id_str)
        except (ValueError, AttributeError):
            return
        pending = _pending.pop(target_id, None)
        base_text = query.message.text if query.message else ""
        if not pending:
            await query.edit_message_text(base_text + "\n\n(no longer pending)")
            return
        if action == "approve":
            allowlist.add_user(target_id)
            await ctx.bot.send_message(chat_id=target_id, text="You're in. Talk to me when you're ready.")
            await query.edit_message_text(base_text + f"\n\n✅ Approved user {target_id}")
        elif action == "deny":
            await ctx.bot.send_message(chat_id=target_id, text="Request denied.")
            await query.edit_message_text(base_text + f"\n\n❌ Denied user {target_id}")

    app = Application.builder().token(token).build()
    app.add_handler(MessageHandler(filters.TEXT | filters.VOICE, on_message))
    app.add_handler(CallbackQueryHandler(on_callback))
    return app


def main() -> None:
    # Warm Whisper model before polling so the first voice turn doesn't
    # block the event loop on a multi-second cold load.
    from telegram_bot.voice import _model
    log.info("warming whisper model...")
    _model()
    log.info("whisper model ready")
    app = _build_app()
    app.run_polling()


if __name__ == "__main__":
    main()
