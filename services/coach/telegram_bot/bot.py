"""Telegram polling bot. Allowlist → (voice|text) → coach service → reply."""
from __future__ import annotations
import asyncio
import logging
import os
from pathlib import Path

from telegram import Update
from telegram.ext import (
    Application, MessageHandler, ContextTypes, filters,
)

from telegram_bot.allowlist import Allowlist
from telegram_bot.coach_client import CoachClient
from telegram_bot.voice import transcribe_voice

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("coach.telegram")


def _build_app() -> Application:
    token = os.environ["TELEGRAM_COACH_BOT_TOKEN"]
    allowlist = Allowlist(Path(os.environ.get("COACH_ACCESS_JSON", "/app/access.json")))
    coach = CoachClient(os.environ.get("COACH_AGENT_URL", "http://coach-agent:8800"))

    async def on_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        if not user or not allowlist.allowed(user.id):
            log.info("rejecting user_id=%s", user and user.id)
            return
        msg = update.effective_message
        if not msg:
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
        try:
            result = await asyncio.to_thread(coach.turn, str(user.id), text)
            await msg.reply_text(result["reply"])
        except Exception as exc:
            log.exception("coach call failed")
            await msg.reply_text("Coach is down. Try again in a minute.")

    app = Application.builder().token(token).build()
    app.add_handler(MessageHandler(filters.TEXT | filters.VOICE, on_message))
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
