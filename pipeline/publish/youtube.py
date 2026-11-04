"""YouTube adapter: wraps upload_draft_short and handles `kind` (spec 2026-11-03-console-operability §7.3)."""
from __future__ import annotations

from pathlib import Path

from pipeline.publishing_config import current_profile, load
from pipeline.youtube_client import upload_draft_short

SHORTS_MAX_S = 180
SHORTS_TAG = "#Shorts"


def publish(run_dir: Path, package: dict) -> dict:
    fields, duration = package["fields"], package["readonly"].get("duration_s")
    if not fields["title"].strip():
        raise ValueError("title is empty: fill it in before publishing")
    description = fields.get("description", "")
    if fields["kind"] == "short":
        if duration is not None and duration > SHORTS_MAX_S:
            raise ValueError(f"video is {duration} s, a short is at most {SHORTS_MAX_S} s: set kind to video")
        if SHORTS_TAG.lower() not in description.lower():
            description = f"{description} {SHORTS_TAG}".strip()
    config = load(current_profile())["youtube"]
    video = next(Path(a["path"]) for a in package["assets"] if a["role"] == "video")
    uploaded = upload_draft_short(video, title=fields["title"], description=description, tags=fields.get("tags", []),
                                  category_id=config["category_id"], privacy_status=fields["privacy_status"],
                                  made_for_kids=config["made_for_kids"])
    return {"id": uploaded.video_id, "url": uploaded.url}
