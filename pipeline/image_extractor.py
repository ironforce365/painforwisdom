"""Smart frame extraction for WordPress featured images.

Strategy:
  1. Probe video duration via ``ffprobe``.
  2. Sample N timestamps in a band around the midpoint
     (default: 10 samples in [midpoint - 20%, midpoint + 20%]).
  3. Extract each candidate frame via ``ffmpeg -ss <ts> -vframes 1``.
  4. Score each frame on Laplacian variance (focus) plus a brightness term
     that penalises over/under-exposed frames.
  5. Pick the best frame, center-crop + resize to the target cover size
     (default 1200x630), encode JPEG at quality 88, return the path.

Hard requirements on the host:
  - ``ffmpeg`` and ``ffprobe`` binaries on PATH.
  - ``opencv-python-headless`` and ``numpy`` Python packages.

The node wraps this module in a try/except so a missing dep degrades the
pipeline to "no featured image" rather than failing the run.
"""
from __future__ import annotations

import json
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import List, Tuple


@dataclass
class FrameCandidate:
    timestamp_s: float
    path: Path
    score: float
    focus: float
    brightness_term: float


class ImageExtractionError(RuntimeError):
    """Raised when ffmpeg, ffprobe, OpenCV, or numpy are unavailable or fail."""


def _require_ffmpeg() -> None:
    for binary in ("ffmpeg", "ffprobe"):
        try:
            subprocess.run(
                [binary, "-version"],
                check=True,
                capture_output=True,
                timeout=10,
            )
        except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            raise ImageExtractionError(
                f"{binary} not available on PATH. Install via: apt install ffmpeg"
            ) from exc


def get_duration_seconds(video_path: Path) -> float:
    """Return the duration of the video in seconds (ffprobe)."""
    _require_ffmpeg()
    result = subprocess.run(
        [
            "ffprobe",
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "json",
            str(video_path),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    payload = json.loads(result.stdout)
    duration_str = payload.get("format", {}).get("duration", "0")
    return float(duration_str)


def sample_candidate_timestamps(
    duration: float,
    *,
    samples: int = 10,
    center: float = 0.5,
    spread: float = 0.2,
) -> List[float]:
    """Return ``samples`` evenly spaced timestamps in
    ``[duration * (center - spread), duration * (center + spread)]``.

    Clamps to ``[0.1, duration - 0.1]`` so ffmpeg always has a real frame.
    """
    if duration <= 0:
        return []
    start = max(0.1, duration * (center - spread))
    end = min(duration - 0.1, duration * (center + spread))
    if end <= start:
        return [duration * center]
    if samples <= 1:
        return [(start + end) / 2.0]
    step = (end - start) / (samples - 1)
    return [start + i * step for i in range(samples)]


def extract_frame(video_path: Path, ts_seconds: float, out_path: Path) -> Path:
    """Extract a single frame at ``ts_seconds`` to ``out_path`` as JPEG."""
    _require_ffmpeg()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-ss", f"{ts_seconds:.3f}",
            "-i", str(video_path),
            "-vframes", "1",
            "-q:v", "2",
            str(out_path),
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )
    return out_path


def _score_components(frame_path: Path) -> Tuple[float, float, float]:
    """Return (focus, brightness_term, combined_score) for a JPEG path.

    focus            = Laplacian variance — higher means sharper edges.
    brightness_term  = inverted distance from the well-exposed band
                       [60, 195] on the 0..255 mean-grayscale axis. 1.0
                       inside the band; falls off toward 0.0 at extremes.
    combined_score   = focus * brightness_term so a perfectly sharp but
                       black/white frame still loses to a slightly softer,
                       well-exposed one.
    """
    try:
        import cv2  # type: ignore
        import numpy as np  # type: ignore
    except ImportError as exc:  # pragma: no cover — surfaced via ImageExtractionError
        raise ImageExtractionError(
            "OpenCV / numpy not installed. Install via: "
            "pip install opencv-python-headless numpy"
        ) from exc

    image = cv2.imread(str(frame_path), cv2.IMREAD_COLOR)
    if image is None:
        return (0.0, 0.0, 0.0)
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    focus = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    mean_brightness = float(np.mean(gray))
    lo, hi = 60.0, 195.0
    if lo <= mean_brightness <= hi:
        brightness_term = 1.0
    else:
        distance = lo - mean_brightness if mean_brightness < lo else mean_brightness - hi
        falloff = max(0.0, 1.0 - (distance / 60.0))
        brightness_term = falloff
    combined = focus * brightness_term
    return (focus, brightness_term, combined)


def score_frame(path: Path) -> float:
    """Public wrapper — combined Laplacian-variance * brightness-term score."""
    return _score_components(path)[2]


def _center_crop_resize(src: Path, dst: Path, size: Tuple[int, int]) -> Path:
    """Center-crop the source image to the target aspect ratio, then resize.

    The crop preserves the centre of the frame, which is where Gonzalo
    typically is when filming a video log.
    """
    try:
        import cv2  # type: ignore
    except ImportError as exc:  # pragma: no cover
        raise ImageExtractionError(
            "OpenCV not installed. Install via: pip install opencv-python-headless"
        ) from exc

    image = cv2.imread(str(src), cv2.IMREAD_COLOR)
    if image is None:
        raise ImageExtractionError(f"Failed to read frame {src}")

    target_w, target_h = size
    target_aspect = target_w / target_h
    h, w = image.shape[:2]
    src_aspect = w / h

    if src_aspect > target_aspect:
        # Source is wider — crop sides.
        new_w = int(h * target_aspect)
        x0 = max(0, (w - new_w) // 2)
        cropped = image[:, x0 : x0 + new_w]
    else:
        # Source is taller — crop top/bottom.
        new_h = int(w / target_aspect)
        y0 = max(0, (h - new_h) // 2)
        cropped = image[y0 : y0 + new_h, :]

    resized = cv2.resize(cropped, (target_w, target_h), interpolation=cv2.INTER_AREA)
    dst.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(dst), resized, [cv2.IMWRITE_JPEG_QUALITY, 88])
    return dst


def pick_and_resize_best(
    video_path: Path,
    out_path: Path,
    *,
    size: Tuple[int, int] = (1200, 630),
    samples: int = 10,
) -> Tuple[Path, float, List[FrameCandidate]]:
    """End-to-end: probe, sample, score, resize. Returns (out_path, best_score, all_candidates).

    Raises ``ImageExtractionError`` if any stage fails. Caller is responsible
    for catching this and degrading gracefully (e.g. "no featured image").
    """
    _require_ffmpeg()
    duration = get_duration_seconds(video_path)
    if duration <= 0:
        raise ImageExtractionError(f"Could not determine duration for {video_path}")

    timestamps = sample_candidate_timestamps(duration, samples=samples)
    if not timestamps:
        raise ImageExtractionError(f"No candidate timestamps for {video_path}")

    candidates: List[FrameCandidate] = []
    with tempfile.TemporaryDirectory(prefix="painforwisdom_frames_") as tmp:
        tmp_dir = Path(tmp)
        for i, ts in enumerate(timestamps):
            frame_path = tmp_dir / f"candidate_{i:02d}.jpg"
            try:
                extract_frame(video_path, ts, frame_path)
            except subprocess.CalledProcessError:
                continue
            focus, brightness_term, score = _score_components(frame_path)
            candidates.append(
                FrameCandidate(
                    timestamp_s=ts,
                    path=frame_path,
                    score=score,
                    focus=focus,
                    brightness_term=brightness_term,
                )
            )

        if not candidates:
            raise ImageExtractionError(f"No frames could be scored for {video_path}")

        best = max(candidates, key=lambda c: c.score)
        _center_crop_resize(best.path, out_path, size)

    return out_path, best.score, candidates
