"""Stage 4d — YouTube upload (parallel branch from extract).

Calls the ``youtube-upload-agent`` to produce title/description/extra tags
from the extraction report, merges the extras with the channel default
tag set in ``config/youtube_metadata.json``, then uploads the source
video as a draft (privacy=private) via the YouTube Data API v3.

Skip rules:
  - ``validator_verdict == "FAIL"``: backfill / replay-aware safety.
  - ``video_path`` missing or unreadable.
  - ``YOUTUBE_ENABLED`` env var not ``"true"`` — pipeline runs forever
    in dormant mode by default. The metadata JSON is still written for
    manual upload, so nothing is lost.
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List

from pipeline.contracts import assert_inputs
from pipeline.llm import call_llm
from pipeline.runtime import (
    PROJECT_ROOT,
    append_metric,
    load_agent_prompt,
    run_telemetry_path,
)
from pipeline.state import State


CONFIG_PATH = PROJECT_ROOT / "config" / "youtube_metadata.json"


def _write_marker(out_dir: Path, name: str, body: str) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / name).write_text(body)


def _load_defaults() -> Dict[str, Any]:
    if CONFIG_PATH.is_file():
        try:
            return json.loads(CONFIG_PATH.read_text())
        except json.JSONDecodeError:
            print(f"[youtube-upload] WARN failed to parse {CONFIG_PATH}; using empty defaults")
    return {}


def _strip_json_fences(text: str) -> str:
    text = text.strip()
    fence = re.match(r"^```(?:json)?\s*(.+?)\s*```$", text, re.DOTALL)
    return fence.group(1).strip() if fence else text


def _parse_agent_response(text: str) -> Dict[str, Any]:
    body = _strip_json_fences(text)
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        # Last-ditch attempt: pull the first JSON object out of the response.
        m = re.search(r"\{.*\}", body, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                pass
    return {"title": "", "description": "", "tags_extra": []}


def _merge_tags(defaults: List[str], extras: List[str], *, limit: int = 25) -> List[str]:
    seen: set[str] = set()
    merged: List[str] = []
    for tag in list(defaults or []) + list(extras or []):
        if not isinstance(tag, str):
            continue
        tag = tag.strip()
        if not tag:
            continue
        key = tag.lower()
        if key in seen:
            continue
        seen.add(key)
        merged.append(tag)
        if len(merged) >= limit:
            break
    return merged


def node_youtube_upload(state: State) -> Dict[str, Any]:
    assert_inputs("youtube_upload", state)
    t0 = time.time()
    print("[youtube-upload] start")

    run_dir = Path(state["run_dir"])
    out_dir = run_dir / "youtube-upload"
    out_dir.mkdir(parents=True, exist_ok=True)

    if state.get("validator_verdict") == "FAIL":
        _write_marker(out_dir, "SKIPPED.md", "YouTube upload skipped: validator_verdict=FAIL\n")
        print("[youtube-upload] skipped (validator FAIL)")
        return {"youtube_skipped": True, "youtube_skip_reason": "validator FAIL"}

    video_path_str = state.get("video_path", "")
    if not video_path_str or not Path(video_path_str).is_file():
        _write_marker(
            out_dir,
            "SKIPPED.md",
            f"YouTube upload skipped: video_path missing or not a file ({video_path_str!r}).\n",
        )
        print("[youtube-upload] skipped (no video)")
        return {"youtube_skipped": True, "youtube_skip_reason": "no video_path"}

    defaults = _load_defaults()
    default_tags = list(defaults.get("default_tags") or [])
    default_description_suffix = str(defaults.get("default_description_suffix") or "").strip()
    category_id = str(defaults.get("default_category_id") or "22")
    privacy_status = str(defaults.get("default_privacy_status") or "private")

    # Always run the metadata agent — even in dormant mode we want the JSON
    # bundle on disk so Gonzalo can paste it into Studio manually.
    system_prompt = load_agent_prompt("youtube-upload-agent.md")
    user_msg = (
        f"Video date: {state.get('video_date','')}\n\n"
        f"## EXTRACTION REPORT\n```\n{state.get('extraction_report','')}\n```\n\n"
        f"## BLOG POST SEED\n{state.get('blog_post_seed','')}\n"
    )
    model = os.environ.get("PIPELINE_MODEL", "claude-sonnet-4-6")
    try:
        result = call_llm(model, system_prompt, user_msg, max_tokens=600)
    except Exception as exc:  # noqa: BLE001
        _write_marker(
            out_dir,
            "METADATA_FAILED.md",
            f"YouTube metadata agent failed: {type(exc).__name__}: {exc}\n",
        )
        print(f"[youtube-upload] metadata agent failed: {exc}")
        return {"youtube_skipped": True, "youtube_skip_reason": f"metadata agent: {exc}"}

    parsed = _parse_agent_response(result.get("text", ""))
    title = (parsed.get("title") or "").strip()
    description = (parsed.get("description") or "").strip()
    if default_description_suffix:
        description = (description + " " + default_description_suffix).strip()
    tags_extra = parsed.get("tags_extra") or []
    if not isinstance(tags_extra, list):
        tags_extra = []
    tags = _merge_tags(default_tags, [str(t) for t in tags_extra])

    metadata = {
        "title": title,
        "description": description,
        "tags": tags,
        "category_id": category_id,
        "privacy_status": privacy_status,
        "video_path": video_path_str,
    }
    (out_dir / "metadata.json").write_text(json.dumps(metadata, indent=2))

    if os.environ.get("YOUTUBE_ENABLED", "").lower() != "true":
        _write_marker(
            out_dir,
            "SKIPPED.md",
            "YouTube upload skipped: YOUTUBE_ENABLED != 'true'.\n"
            "Metadata bundle written to metadata.json for manual upload.\n",
        )
        duration = time.time() - t0
        append_metric(
            run_telemetry_path(state["run_dir"]),
            "youtube-upload",
            duration_s=round(duration, 2),
            skipped=True,
            reason="dormant",
            title=title,
            tag_count=len(tags),
        )
        print(f"[youtube-upload] dormant {duration:.1f}s title={title!r}")
        return {
            "youtube_skipped": True,
            "youtube_skip_reason": "dormant (YOUTUBE_ENABLED!=true)",
        }

    if not title:
        _write_marker(
            out_dir,
            "SKIPPED.md",
            "YouTube upload skipped: agent returned empty title.\n",
        )
        return {"youtube_skipped": True, "youtube_skip_reason": "empty title"}

    # Live upload path.
    try:
        from pipeline.youtube_client import upload_draft_short
        upload_result = upload_draft_short(
            Path(video_path_str),
            title=title,
            description=description,
            tags=tags,
            category_id=category_id,
            privacy_status=privacy_status,
        )
    except Exception as exc:  # noqa: BLE001
        _write_marker(
            out_dir,
            "UPLOAD_FAILED.md",
            f"YouTube upload failed: {type(exc).__name__}: {exc}\n",
        )
        duration = time.time() - t0
        append_metric(
            run_telemetry_path(state["run_dir"]),
            "youtube-upload",
            duration_s=round(duration, 2),
            error=f"{type(exc).__name__}: {exc}",
        )
        print(f"[youtube-upload] upload failed: {exc}")
        return {
            "youtube_skipped": True,
            "youtube_skip_reason": f"upload error: {exc}",
        }

    (out_dir / "upload.json").write_text(
        json.dumps(
            {
                "video_id": upload_result.video_id,
                "url": upload_result.url,
                "title": title,
                "description": description,
                "tags": tags,
            },
            indent=2,
        )
    )

    duration = time.time() - t0
    append_metric(
        run_telemetry_path(state["run_dir"]),
        "youtube-upload",
        duration_s=round(duration, 2),
        video_id=upload_result.video_id,
        url=upload_result.url,
        tag_count=len(tags),
    )
    print(f"[youtube-upload] done {duration:.1f}s url={upload_result.url}")
    return {
        "youtube_video_id": upload_result.video_id,
        "youtube_url": upload_result.url,
        "youtube_skipped": False,
    }
