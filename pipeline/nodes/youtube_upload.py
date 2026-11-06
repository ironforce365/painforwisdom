"""YouTube upload: builds the C9 package from ``metadata.json`` and C10, then follows the mode table
(spec 2026-11-03-console-operability §1.3, §7.3).

Skips without a package on ``validator_verdict == "FAIL"`` or a missing video.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, Optional

from pipeline.contracts import assert_inputs
from pipeline.image_extractor import get_duration_seconds
from pipeline.nodes.youtube_metadata import skip_reason
from pipeline.publish import package as publish_package
from pipeline.publish import run_target, specs, video_meta
from pipeline.publishing_config import current_profile, load, run_language
from pipeline.runtime import append_metric, run_telemetry_path
from pipeline.state import State

FIELD_SPECS = specs.YOUTUBE


def _write_marker(out_dir: Path, name: str, body: str) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / name).write_text(body)


def _duration_s(video: Path) -> Optional[float]:
    try:
        return round(get_duration_seconds(video), 1)
    except Exception as exc:  # noqa: BLE001 - duration only guards the Shorts limit; the upload can still go ahead
        print(f"[youtube-upload] WARN no duration: {type(exc).__name__}: {exc}")
        return None


def _read_metadata(state: State) -> Dict[str, Any]:
    path = Path(state.get("youtube_metadata_path") or "")
    return json.loads(path.read_text()) if path.is_file() else {"warning": "no metadata.json"}


def node_youtube_upload(state: State) -> Dict[str, Any]:
    assert_inputs("youtube_upload", state)
    t0 = time.time()
    print("[youtube-upload] start")
    run_dir = Path(state["run_dir"])
    out_dir = run_dir / "youtube-upload"
    out_dir.mkdir(parents=True, exist_ok=True)
    telemetry = run_telemetry_path(state["run_dir"])

    reason = skip_reason(state)
    if reason:
        _write_marker(out_dir, "SKIPPED.md", f"YouTube upload skipped: {reason}.\n")
        append_metric(telemetry, "youtube-upload", duration_s=round(time.time() - t0, 2), skipped=True, reason=reason)
        print(f"[youtube-upload] skipped ({reason})")
        return {"youtube_skipped": True, "youtube_skip_reason": reason}

    # A retry or resume replays the node: never upload twice.
    existing = publish_package.read(run_dir, "youtube")
    if existing and existing["status"] == "published":
        print("[youtube-upload] already published")
        return {"youtube_video_id": existing["result"]["id"], "youtube_url": existing["result"]["url"],
                "youtube_skipped": False}
    if existing and existing["status"] == "publishing":  # a publisher holds it: its result lands there
        append_metric(telemetry, "youtube-upload", duration_s=round(time.time() - t0, 2), skipped=True, reason="publishing")
        print("[youtube-upload] a publish is in progress, leaving it alone")
        return {"youtube_skipped": True, "youtube_skip_reason": "publishing"}

    config = load(current_profile())["youtube"]
    mode = publish_package.replay_mode(existing, config["mode"])
    metadata = _read_metadata(state)
    title, tags = metadata.get("title", ""), metadata.get("tags", [])

    if mode != "off":
        video = Path(state["video_path"])
        warning = metadata.get("warning")
        publish_package.write(run_dir, publish_package.kept_on_replay(existing, publish_package.new_package(
            "youtube",
            mode,
            {
                "title": title,
                "description": metadata.get("description", "")[:FIELD_SPECS["description"]["max"]],
                "tags": tags,
                "privacy_status": config["privacy_status"],
                "kind": config["kind"],
                **video_meta.youtube_fields(video, run_language(state.get("language"))),
            },
            FIELD_SPECS,
            readonly={"duration_s": _duration_s(video), **({"warning": warning} if warning else {})},
            assets=[{"role": "video", "path": str(video)}],
        )))

    if mode != "auto":
        _write_marker(out_dir, "SKIPPED.md", f"YouTube upload skipped: mode={mode}.\n")
        duration = time.time() - t0
        append_metric(telemetry, "youtube-upload", duration_s=round(duration, 2), skipped=True, reason=mode,
                      title=title, tag_count=len(tags))
        print(f"[youtube-upload] {mode} {duration:.1f}s title={title!r}")
        return {"youtube_skipped": True, "youtube_skip_reason": mode}

    # Any error lands in the package and never raises into the graph.
    published = run_target(run_dir, "youtube")
    duration = time.time() - t0
    if published["status"] != "published":
        append_metric(telemetry, "youtube-upload", duration_s=round(duration, 2), error=published["error"])
        print(f"[youtube-upload] failed {duration:.1f}s: {published['error']}")
        return {"youtube_skipped": True, "youtube_skip_reason": f"publish error: {published['error']}"}
    video_id, url = published["result"]["id"], published["result"]["url"]
    append_metric(telemetry, "youtube-upload", duration_s=round(duration, 2), video_id=video_id, url=url,
                  title=title, tag_count=len(tags))
    print(f"[youtube-upload] done {duration:.1f}s url={url}")
    return {"youtube_video_id": video_id, "youtube_url": url, "youtube_skipped": False}
