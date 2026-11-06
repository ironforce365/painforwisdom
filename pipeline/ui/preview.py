"""A browser-playable copy of a package's video (spec 2026-11-05-console-end-to-end section 2 P12).

For viewing only: the package keeps pointing at the original and the upload sends that. The copy sits next to the
run's langgraph folder, not in it, so it is never listed among the run's files.
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
from pathlib import Path

from pipeline.ui.settings import Settings

HDR_TRANSFERS = {"arib-std-b67", "smpte2084"}  # HLG, PQ
TIMEOUT_S = 1800
ERROR_CHARS = 500
FAKE_STEP_MS = 300
SCALE = "scale=1280:1280:force_original_aspect_ratio=decrease:force_divisible_by=2"
TONE_MAP = "zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,tonemap=hable:desat=0,zscale=t=bt709:m=bt709:r=tv"

_REPO = Path(__file__).resolve().parents[2]
_jobs: set[Path] = set()
_lock = threading.Lock()


def is_hdr(src: Path) -> bool:
    """A failed probe reads as not HDR: ffmpeg then reports what is wrong with the file."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=color_transfer",
             "-of", "default=nw=1:nk=1", str(src)], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return out.stdout.strip() in HDR_TRANSFERS


def ffmpeg_argv(src: Path, dest: Path, hdr: bool) -> list[str]:
    """H.264 and AAC, long side capped at 1280, upright. ffmpeg applies the container's rotation by itself."""
    video_filter = ",".join([SCALE, *([TONE_MAP] if hdr else []), "format=yuv420p"])
    return ["nice", "-n", "10", "ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(src), "-vf", video_filter,
            "-map", "0:v:0", "-map", "0:a:0?", "-map_metadata", "-1", "-c:v", "libx264", "-preset", "veryfast",
            "-crf", "26", "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(dest)]


def _argv(settings: Settings, src: Path, dest: Path) -> list[str]:
    if settings.runner == "fake":
        return [sys.executable, "-m", "pipeline.ui.fake_runner", "preview", "--step-ms", str(FAKE_STEP_MS), str(dest)]
    return ffmpeg_argv(src, dest, is_hdr(src))


def _copy_path(rd: Path, target: str, role: str) -> Path:
    return rd.parent / "console-preview" / f"{target}-{role}.mp4"


def _error_path(copy: Path) -> Path:
    return copy.with_suffix(".error")


def _convert(settings: Settings, src: Path, copy: Path) -> None:
    part = copy.with_suffix(".part.mp4")  # a fixed name: a conversion cut off by a restart is simply overwritten
    try:
        done = subprocess.run(_argv(settings, src, part), capture_output=True, text=True, errors="replace",
                              timeout=TIMEOUT_S, cwd=_REPO)
        if done.returncode != 0:
            raise RuntimeError(done.stderr.strip()[-ERROR_CHARS:] or f"ffmpeg exited with {done.returncode}")
        os.replace(part, copy)
    except subprocess.TimeoutExpired:
        _fail(copy, part, f"timed out after {TIMEOUT_S}s")
    except (OSError, RuntimeError) as e:
        _fail(copy, part, str(e))
    finally:
        with _lock:
            _jobs.discard(copy)


def _fail(copy: Path, part: Path, reason: str) -> None:
    part.unlink(missing_ok=True)
    copy.parent.mkdir(parents=True, exist_ok=True)
    _error_path(copy).write_text(reason)


def state(rd: Path, target: str, role: str) -> dict:
    copy = _copy_path(rd, target, role)
    with _lock:
        if copy in _jobs:
            return {"status": "converting", "error": None}
    if copy.is_file():
        return {"status": "ready", "error": None}
    error = _error_path(copy)
    return {"status": "failed", "error": error.read_text()} if error.is_file() else {"status": "none", "error": None}


def start(settings: Settings, rd: Path, target: str, role: str, src: Path) -> dict:
    """Convert unless a copy exists or is on its way. Asking again after a failure retries."""
    copy = _copy_path(rd, target, role)
    with _lock:
        if copy in _jobs:
            return {"status": "converting", "error": None}
        if copy.is_file():
            return {"status": "ready", "error": None}
        copy.parent.mkdir(parents=True, exist_ok=True)
        _error_path(copy).unlink(missing_ok=True)
        _jobs.add(copy)
    threading.Thread(target=_convert, args=(settings, src, copy), daemon=True).start()
    return {"status": "converting", "error": None}


def ready_copy(rd: Path, target: str, role: str) -> Path | None:
    copy = _copy_path(rd, target, role)
    return copy if copy.is_file() else None
