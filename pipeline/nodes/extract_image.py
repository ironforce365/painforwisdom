"""Stage 4c — extract a featured image from the source video.

Parallel branch from ``extract``. Writes the JPEG into the run directory at
``langgraph/wordpress-draft/featured.jpg`` so the WordPress draft node can
pick it up without re-deriving the path.

Skip rules:
  - ``validator_verdict == "FAIL"`` (defensive; validator runs later but
    backfill or retries may re-execute this node with verdict already set).
  - ``video_path`` missing or file not found — common in backfill / test
    modes where the pipeline replays from a transcript.

Failures inside the extractor (ffmpeg missing, OpenCV missing, malformed
video) are caught and reported via ``image_extraction_failed=True``; the
WordPress node must tolerate a missing image.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Dict

from pipeline.contracts import assert_inputs
from pipeline.image_extractor import ImageExtractionError, pick_and_resize_best
from pipeline.runtime import append_metric, run_telemetry_path
from pipeline.state import State


def _write_marker(out_dir: Path, name: str, body: str) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / name).write_text(body)


def node_extract_image(state: State) -> Dict[str, Any]:
    assert_inputs("extract_image", state)
    t0 = time.time()
    print("[extract-image] start")

    run_dir = Path(state["run_dir"])
    out_dir = run_dir / "wordpress-draft"
    out_dir.mkdir(parents=True, exist_ok=True)

    if state.get("validator_verdict") == "FAIL":
        _write_marker(
            out_dir,
            "IMAGE_SKIPPED.md",
            "Image extraction skipped: validator_verdict=FAIL\n",
        )
        print("[extract-image] skipped (validator FAIL)")
        return {"image_extraction_failed": True}

    video_path_str = state.get("video_path", "")
    if not video_path_str or not Path(video_path_str).is_file():
        _write_marker(
            out_dir,
            "IMAGE_SKIPPED.md",
            f"Image extraction skipped: video_path missing or not a file (got {video_path_str!r}).\n",
        )
        print("[extract-image] skipped (no video)")
        return {"image_extraction_failed": True}

    video_path = Path(video_path_str)
    out_path = out_dir / "featured.jpg"

    try:
        result_path, score, candidates = pick_and_resize_best(video_path, out_path)
    except ImageExtractionError as exc:
        _write_marker(
            out_dir,
            "IMAGE_EXTRACTION_FAILED.md",
            f"Image extraction failed: {type(exc).__name__}: {exc}\n",
        )
        duration = time.time() - t0
        append_metric(
            run_telemetry_path(state["run_dir"]),
            "extract-image",
            duration_s=round(duration, 2),
            error=f"{type(exc).__name__}: {exc}",
        )
        print(f"[extract-image] failed {duration:.1f}s: {exc}")
        return {"image_extraction_failed": True}
    except Exception as exc:  # noqa: BLE001 — last-resort safety net
        _write_marker(
            out_dir,
            "IMAGE_EXTRACTION_FAILED.md",
            f"Image extraction crashed: {type(exc).__name__}: {exc}\n",
        )
        duration = time.time() - t0
        append_metric(
            run_telemetry_path(state["run_dir"]),
            "extract-image",
            duration_s=round(duration, 2),
            error=f"crash:{type(exc).__name__}: {exc}",
        )
        print(f"[extract-image] crash {duration:.1f}s: {exc}")
        return {"image_extraction_failed": True}

    duration = time.time() - t0
    append_metric(
        run_telemetry_path(state["run_dir"]),
        "extract-image",
        duration_s=round(duration, 2),
        score=round(score, 2),
        candidates=len(candidates),
        path=str(result_path),
    )
    print(f"[extract-image] done {duration:.1f}s score={score:.1f} path={result_path}")
    return {
        "featured_image_path": str(result_path),
        "featured_image_score": float(score),
        "image_extraction_failed": False,
    }
