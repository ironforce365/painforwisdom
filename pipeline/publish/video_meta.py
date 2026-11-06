"""What a video's own tags say about where and when it was recorded. Standard library only: the UI server imports it."""
from __future__ import annotations

import json
import re
import subprocess
from datetime import datetime
from pathlib import Path

_ISO6709 = re.compile(r"^([+-]\d+(?:\.\d+)?)([+-]\d+(?:\.\d+)?)")


def _local_day(value: object) -> str:
    """Phones record UTC: an evening run belongs to the day it happened on here."""
    try:
        return datetime.fromisoformat(str(value)).astimezone().date().isoformat()
    except ValueError:
        return ""


def _coarse_location(value: object) -> str:
    """About 1 km: enough to say where the video is from, without publishing a front door."""
    found = _ISO6709.match(str(value))
    return f"{float(found.group(1)):.2f}, {float(found.group(2)):.2f}" if found else ""


def recorded(video: Path) -> dict[str, str]:
    """{recording_date, location}, each empty when the video does not say: the form asks instead."""
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format_tags", "-of", "json", str(video)],
                             capture_output=True, text=True, timeout=30).stdout
        tags = json.loads(out).get("format", {}).get("tags", {})
    except (OSError, ValueError, subprocess.TimeoutExpired):
        tags = {}
    return {"recording_date": _local_day(tags.get("creation_time", "")), "location": _coarse_location(tags.get("location", ""))}


def youtube_fields(video: Path, language: str) -> dict[str, str]:
    """The package fields these fill, shared by the upload node and the console's prepare."""
    return {**recorded(video), "audio_language": language, "text_language": language}
