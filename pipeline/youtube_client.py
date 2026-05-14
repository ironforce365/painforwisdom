"""YouTube Data API v3 thin client for uploading draft shorts.

Auth model: long-lived **refresh token** captured once via
``scripts/youtube_oauth_setup.py``. The pipeline never opens a browser at
runtime — it just exchanges the refresh token for a fresh access token via
google-auth's built-in refresh flow.

Required env vars:
  - ``YOUTUBE_CLIENT_ID``
  - ``YOUTUBE_CLIENT_SECRET``
  - ``YOUTUBE_REFRESH_TOKEN``

Heavy deps (``google-api-python-client``, ``google-auth-httplib2``,
``google-auth-oauthlib``) are imported lazily so the rest of the pipeline
keeps working when YouTube is dormant.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional


class YouTubeClientError(RuntimeError):
    """Raised when YouTube creds are missing or the API call fails."""


@dataclass
class UploadResult:
    video_id: str
    url: str
    raw: Dict[str, Any]


def _require_credentials() -> Dict[str, str]:
    missing = [
        var
        for var in ("YOUTUBE_CLIENT_ID", "YOUTUBE_CLIENT_SECRET", "YOUTUBE_REFRESH_TOKEN")
        if not os.environ.get(var)
    ]
    if missing:
        raise YouTubeClientError(
            "YouTube creds missing: "
            + ", ".join(missing)
            + ". Run scripts/youtube_oauth_setup.py to capture them."
        )
    return {
        "client_id": os.environ["YOUTUBE_CLIENT_ID"],
        "client_secret": os.environ["YOUTUBE_CLIENT_SECRET"],
        "refresh_token": os.environ["YOUTUBE_REFRESH_TOKEN"],
    }


def build_youtube_service():  # type: ignore[no-untyped-def]
    """Return an authorised ``googleapiclient.discovery.Resource`` for YouTube v3."""
    try:
        from google.auth.transport.requests import Request  # type: ignore
        from google.oauth2.credentials import Credentials  # type: ignore
        from googleapiclient.discovery import build  # type: ignore
    except ImportError as exc:
        raise YouTubeClientError(
            "google-api-python-client / google-auth-* not installed. "
            "Run: pip install google-api-python-client google-auth google-auth-oauthlib"
        ) from exc

    creds_input = _require_credentials()
    creds = Credentials(
        token=None,
        refresh_token=creds_input["refresh_token"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=creds_input["client_id"],
        client_secret=creds_input["client_secret"],
        scopes=["https://www.googleapis.com/auth/youtube.upload"],
    )
    creds.refresh(Request())
    return build("youtube", "v3", credentials=creds, cache_discovery=False)


def upload_draft_short(
    video_path: Path,
    *,
    title: str,
    description: str,
    tags: List[str],
    category_id: str = "22",
    privacy_status: str = "private",
    made_for_kids: bool = False,
    max_retries: int = 3,
    initial_backoff_s: float = 4.0,
) -> UploadResult:
    """Upload ``video_path`` to YouTube as a draft (privacy=private by default).

    Drafts on the @painforwisdom channel: the canonical pattern is
    ``privacyStatus="private"`` (YouTube has no formal "draft" state for
    public videos; private + ``selfDeclaredMadeForKids=False`` is the
    closest equivalent and matches how Studio represents an unpublished
    short).
    """
    if not video_path.is_file():
        raise YouTubeClientError(f"video file not found: {video_path}")

    try:
        from googleapiclient.errors import HttpError  # type: ignore
        from googleapiclient.http import MediaFileUpload  # type: ignore
    except ImportError as exc:
        raise YouTubeClientError(
            "googleapiclient not installed. "
            "Run: pip install google-api-python-client"
        ) from exc

    youtube = build_youtube_service()
    body: Dict[str, Any] = {
        "snippet": {
            "title": title,
            "description": description,
            "tags": tags,
            "categoryId": category_id,
        },
        "status": {
            "privacyStatus": privacy_status,
            "selfDeclaredMadeForKids": made_for_kids,
        },
    }
    media = MediaFileUpload(str(video_path), chunksize=-1, resumable=True)

    attempt = 0
    last_exc: Optional[Exception] = None
    backoff = initial_backoff_s
    while attempt <= max_retries:
        try:
            request = youtube.videos().insert(  # type: ignore[attr-defined]
                part="snippet,status",
                body=body,
                media_body=media,
            )
            response = None
            while response is None:
                _status, response = request.next_chunk()
            video_id = response.get("id") or ""
            if not video_id:
                raise YouTubeClientError(f"upload returned no video_id: {response}")
            return UploadResult(
                video_id=video_id,
                url=f"https://youtu.be/{video_id}",
                raw=response,
            )
        except HttpError as exc:
            status = getattr(getattr(exc, "resp", None), "status", None)
            try:
                status_int = int(status) if status is not None else 0
            except (TypeError, ValueError):
                status_int = 0
            if status_int >= 500 and attempt < max_retries:
                last_exc = exc
                time.sleep(backoff)
                backoff *= 2
                attempt += 1
                continue
            raise YouTubeClientError(f"YouTube upload failed (HTTP {status_int}): {exc}") from exc
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            raise YouTubeClientError(f"YouTube upload crashed: {exc}") from exc

    raise YouTubeClientError(
        f"YouTube upload retries exhausted after {max_retries}: {last_exc}"
    )
