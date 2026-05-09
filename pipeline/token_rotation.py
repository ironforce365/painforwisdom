"""Reads the Anthropic OAuth token from ~/.claude/.credentials.json.

The Claude CLI auto-rotates the token in this file as it refreshes. We read
fresh per pipeline run (and on auth errors mid-run) so a long-running
session doesn't fail with `Invalid authentication credentials` after a refresh.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional


CREDENTIALS_PATH = Path.home() / ".claude" / ".credentials.json"


def read_oauth_token() -> Optional[str]:
    """Return the current Pro/Max OAuth bearer token, or None if not configured.

    Schema (as written by `claude setup-token`):
        {
          "claudeAiOauth": {
            "accessToken": "sk-ant-oat01-...",
            "refreshToken": "...",
            "expiresAt": <unix-ms>,
            ...
          }
        }
    """
    if not CREDENTIALS_PATH.is_file():
        return None
    try:
        data = json.loads(CREDENTIALS_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    oauth = data.get("claudeAiOauth") or {}
    token = oauth.get("accessToken")
    if isinstance(token, str) and token:
        return token
    return None
