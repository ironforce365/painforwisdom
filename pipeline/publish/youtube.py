"""YouTube adapter: wraps upload_draft_short and handles `kind` (spec 2026-11-03-console-operability §7.3)."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from pipeline.image_extractor import get_duration_seconds
from pipeline.publishing_config import current_profile, load
from pipeline.youtube_client import upload_draft_short

SHORTS_MAX_S = 180
SHORTS_TAG = "#Shorts"


def _measured(video: Path) -> Optional[float]:
    """A package the console prepared records no duration, and the Shorts limit still has to hold."""
    try:
        return round(get_duration_seconds(video), 1)
    except Exception as exc:  # noqa: BLE001 - only this guard needs the duration; the upload can still go ahead
        print(f"[publish] WARN cannot measure {video.name}, the {SHORTS_MAX_S} s Shorts limit goes unchecked: "
              f"{type(exc).__name__}: {exc}")
        return None


def publish(run_dir: Path, package: dict) -> dict:
    fields, duration = package["fields"], package["readonly"].get("duration_s")
    if not fields["title"].strip():
        raise ValueError("title is empty: fill it in before publishing")
    description = fields.get("description", "")
    video = next(Path(a["path"]) for a in package["assets"] if a["role"] == "video")
    if fields["kind"] == "short":
        if duration is None:
            duration = _measured(video)
        if duration is not None and duration > SHORTS_MAX_S:
            raise ValueError(f"video is {duration} s, a short is at most {SHORTS_MAX_S} s: set kind to video")
        if SHORTS_TAG.lower() not in description.lower():
            description = f"{description} {SHORTS_TAG}".strip()
    config = load(current_profile())["youtube"]
    uploaded = upload_draft_short(video, title=fields["title"], description=description, tags=fields.get("tags", []),
                                  category_id=config["category_id"], privacy_status=fields["privacy_status"],
                                  made_for_kids=config["made_for_kids"])
    return {"id": uploaded.video_id, "url": uploaded.url}
