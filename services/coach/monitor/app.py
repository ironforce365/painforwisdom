"""Monitoring web service (F2 + F3).

A small read-only FastAPI app over the conversation-log volume the Telegram bot
writes (``COACH_CONVO_LOG_DIR``). It serves:

  GET /                       single-page UI (user list + conversation viewer)
  GET /api/users              [{user_id, name, last_ts, last_text, ...}] desc
  GET /api/conversations/{id} {user_id, messages:[{ts, role, text, name}]}

Conversation reads are byte-capped (default 5MB, overridable via ?max_bytes) so
a long history can't blow up the browser — the cap is enforced in
ConversationLog by tailing the file. This service never calls the coach or
mutates state; it only reads JSONL."""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from telegram_bot.conversation_log import DEFAULT_MAX_BYTES, ConversationLog

_INDEX_HTML = (Path(__file__).parent / "static" / "index.html").read_text(
    encoding="utf-8"
)


def _log() -> ConversationLog:
    return ConversationLog(
        Path(os.environ.get("COACH_CONVO_LOG_DIR", "/state/conversations"))
    )


def create_app() -> FastAPI:
    app = FastAPI(title="painforwisdom-coach-monitor")

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return _INDEX_HTML

    @app.get("/api/users")
    def users() -> dict:
        return {"users": _log().list_users()}

    @app.get("/api/conversations/{user_id}")
    def conversation(user_id: str, max_bytes: int = DEFAULT_MAX_BYTES) -> dict:
        messages = _log().read_conversation(user_id, max_bytes=max_bytes)
        return {"user_id": user_id, "messages": messages}

    return app


app = create_app()
