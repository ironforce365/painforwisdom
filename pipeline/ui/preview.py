"""A browser-playable copy of a package's video (spec 2026-11-05-console-end-to-end section 2 P12, P13).

For viewing only: the package keeps pointing at the original and the upload sends that. A run starts the copy itself
(pipeline.video_preview); this module reports on it, and converts on demand when nothing else did.
"""
from __future__ import annotations

import subprocess  # noqa: F401 - tests stub subprocess.run through here
import sys
import threading
from pathlib import Path

from pipeline import video_preview
from pipeline.ui.settings import Settings
from pipeline.video_preview import ERROR_CHARS, TIMEOUT_S, ffmpeg_argv, is_hdr  # noqa: F401 - this module's API too

FAKE_STEP_MS = 300

_jobs: set[Path] = set()
_lock = threading.Lock()


def _argv(settings: Settings, src: Path, dest: Path) -> list[str]:
    if settings.runner == "fake":
        return [sys.executable, "-m", "pipeline.ui.fake_runner", "preview", "--step-ms", str(FAKE_STEP_MS), str(dest)]
    return ffmpeg_argv(src, dest, is_hdr(src))


def _convert(settings: Settings, src: Path, copy: Path) -> None:
    try:
        video_preview.convert(_argv(settings, src, video_preview.part_path(copy)), copy)
    finally:
        with _lock:
            _jobs.discard(copy)


def _busy(copy: Path) -> bool:
    """Call holding _lock: this console's thread, or a run's own process."""
    return copy in _jobs or video_preview.job_running(copy)


def state(rd: Path, target: str, role: str) -> dict:
    copy = video_preview.copy_path(rd, target, role)
    with _lock:
        if _busy(copy):
            return {"status": "converting", "error": None}
    if copy.is_file():
        return {"status": "ready", "error": None}
    error = video_preview.error_path(copy)
    return {"status": "failed", "error": error.read_text()} if error.is_file() else {"status": "none", "error": None}


def start(settings: Settings, rd: Path, target: str, role: str, src: Path) -> dict:
    """Convert unless a copy exists or is on its way. Asking again after a failure retries."""
    copy = video_preview.copy_path(rd, target, role)
    with _lock:
        if _busy(copy):
            return {"status": "converting", "error": None}
        if copy.is_file():
            return {"status": "ready", "error": None}
        copy.parent.mkdir(parents=True, exist_ok=True)
        video_preview.error_path(copy).unlink(missing_ok=True)
        _jobs.add(copy)
    threading.Thread(target=_convert, args=(settings, src, copy), daemon=True).start()
    return {"status": "converting", "error": None}


def converting(rd: Path) -> bool:
    """A conversion of this run is on its way: it still writes under the run's folder."""
    folder = rd.parent / "console-preview"
    with _lock:
        if any(copy.parent == folder for copy in _jobs):
            return True
    return any(video_preview.job_running(job.with_suffix(".mp4")) for job in folder.glob("*.job"))


def ready_copy(rd: Path, target: str, role: str) -> Path | None:
    copy = video_preview.copy_path(rd, target, role)
    return copy if copy.is_file() else None
