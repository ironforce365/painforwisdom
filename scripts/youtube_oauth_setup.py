"""One-shot helper: obtain a YouTube Data API v3 refresh token.

Prerequisites:
  1. Create a Google Cloud project, enable "YouTube Data API v3".
  2. OAuth consent screen → External → add Gonzalo's gmail as a test user.
  3. Credentials → Create OAuth client ID → "Desktop app" → download the
     JSON. Save it as
     ``~/.config/painforwisdom/youtube_client_secret.json``
     or wherever ``YOUTUBE_CLIENT_SECRETS_PATH`` points.

Run this script. A browser opens, you grant the
``https://www.googleapis.com/auth/youtube.upload`` scope, and the script
prints the refresh token.

Copy the refresh token (and the client id / secret if you do not already
have them in ``.env``) into your ``.env``:

    YOUTUBE_CLIENT_ID=<from JSON>
    YOUTUBE_CLIENT_SECRET=<from JSON>
    YOUTUBE_REFRESH_TOKEN=<printed by this script>

After that, ``YOUTUBE_ENABLED=true`` activates the pipeline upload node.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

DEFAULT_SECRETS_PATH = Path.home() / ".config" / "painforwisdom" / "youtube_client_secret.json"
SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]


def main() -> int:
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow  # type: ignore
    except ImportError:
        print(
            "✗ google-auth-oauthlib not installed. "
            "Run: pip install google-auth-oauthlib google-api-python-client",
            file=sys.stderr,
        )
        return 2

    secrets_path = Path(os.environ.get("YOUTUBE_CLIENT_SECRETS_PATH") or DEFAULT_SECRETS_PATH)
    if not secrets_path.is_file():
        print(
            f"✗ client_secret JSON not found at {secrets_path}.\n"
            "Download it from Google Cloud Console → APIs & Services → Credentials.",
            file=sys.stderr,
        )
        return 2

    flow = InstalledAppFlow.from_client_secrets_file(str(secrets_path), SCOPES)
    creds = flow.run_local_server(port=0, prompt="consent", access_type="offline")

    payload = json.loads(secrets_path.read_text())
    installed = payload.get("installed") or payload.get("web") or {}
    print("\n========== YouTube OAuth captured ==========")
    print(f"YOUTUBE_CLIENT_ID={installed.get('client_id','')}")
    print(f"YOUTUBE_CLIENT_SECRET={installed.get('client_secret','')}")
    print(f"YOUTUBE_REFRESH_TOKEN={creds.refresh_token}")
    print("============================================")
    print(
        "\nPaste the three lines above into your .env. Keep the refresh "
        "token secret — it is the equivalent of a long-lived password."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
