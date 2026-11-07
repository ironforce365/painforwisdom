"""The browser-playable copy of a package's video (spec 2026-11-07-preview-at-run-end-design). Standard library only.

A run starts it as soon as its YouTube package exists, in a process of its own, and never waits for it: the copy is
usually ready by the time anyone opens the package. The console reads its progress from files beside the copy, and
converts on its own when no run did (an older run, or a video that looked playable).

    <run>/console-preview/<target>-<role>.mp4        the copy, once whole
    <run>/console-preview/<target>-<role>.part.mp4   while ffmpeg writes it
    <run>/console-preview/<target>-<role>.error      why the last conversion failed
    <run>/console-preview/<target>-<role>.job        pid of a conversion running in its own process
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

HDR_TRANSFERS = {"arib-std-b67", "smpte2084"}  # HLG, PQ
PLAYABLE_CODECS = {"h264"}  # what every browser decodes; anything else gets a copy up front
TIMEOUT_S = 1800
ERROR_CHARS = 500
SCALE = "scale=1280:1280:force_original_aspect_ratio=decrease:force_divisible_by=2"
TONE_MAP = "zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,tonemap=hable:desat=0,zscale=t=bt709:m=bt709:r=tv"

REPO = Path(__file__).resolve().parents[1]


def _probe(src: Path, entry: str) -> Optional[str]:
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", f"stream={entry}",
             "-of", "default=nw=1:nk=1", str(src)], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out.stdout.strip() or None


def is_hdr(src: Path) -> bool:
    """A failed probe reads as not HDR: ffmpeg then reports what is wrong with the file."""
    return _probe(src, "color_transfer") in HDR_TRANSFERS


def needs_copy(src: Path) -> bool:
    """Convert up front only what surely needs it. A failed probe says no: the console still converts on demand."""
    codec = _probe(src, "codec_name")
    return codec is not None and codec not in PLAYABLE_CODECS


def ffmpeg_argv(src: Path, dest: Path, hdr: bool) -> list[str]:
    """H.264 and AAC, long side capped at 1280, upright. ffmpeg applies the container's rotation by itself."""
    video_filter = ",".join([SCALE, *([TONE_MAP] if hdr else []), "format=yuv420p"])
    return ["nice", "-n", "10", "ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(src), "-vf", video_filter,
            "-map", "0:v:0", "-map", "0:a:0?", "-map_metadata", "-1", "-c:v", "libx264", "-preset", "veryfast",
            "-crf", "26", "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(dest)]


def copy_path(run_dir: Path, target: str, role: str) -> Path:
    """Beside the run's langgraph folder, not in it, so it is never listed among the run's files."""
    return run_dir.parent / "console-preview" / f"{target}-{role}.mp4"


def part_path(copy: Path) -> Path:
    return copy.with_suffix(".part.mp4")  # a fixed name: a conversion cut off by a restart is simply overwritten


def error_path(copy: Path) -> Path:
    return copy.with_suffix(".error")


def job_path(copy: Path) -> Path:
    return copy.with_suffix(".job")


def _alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except PermissionError:
        return True
    except (ProcessLookupError, OverflowError):
        return False
    return True


def job_running(copy: Path) -> bool:
    """A conversion in a process of its own is on its way. A job file whose process is gone is left over: ignored."""
    try:
        return _alive(int(job_path(copy).read_text().strip()))
    except (OSError, ValueError):
        return False


def convert(argv: list[str], copy: Path) -> None:
    """Run `argv`, which writes part_path(copy), then put the copy in place, or record why not. Never raises."""
    part = part_path(copy)
    try:
        done = subprocess.run(argv, capture_output=True, text=True, errors="replace", timeout=TIMEOUT_S, cwd=REPO)
        if done.returncode != 0:
            raise RuntimeError(done.stderr.strip()[-ERROR_CHARS:] or f"ffmpeg exited with {done.returncode}")
        os.replace(part, copy)
    except subprocess.TimeoutExpired:
        fail(copy, f"timed out after {TIMEOUT_S}s")
    except (OSError, RuntimeError) as e:
        fail(copy, str(e))


def fail(copy: Path, reason: str) -> None:
    part_path(copy).unlink(missing_ok=True)
    copy.parent.mkdir(parents=True, exist_ok=True)
    error_path(copy).write_text(reason)


def start_in_background(run_dir: Path, target: str, role: str, src: Path) -> bool:
    """Start converting in a detached process, unless there is nothing to do. True if it started. Never waits."""
    copy = copy_path(run_dir, target, role)
    if copy.is_file() or job_running(copy) or not src.is_file() or not needs_copy(src):
        return False
    copy.parent.mkdir(parents=True, exist_ok=True)
    error_path(copy).unlink(missing_ok=True)
    proc = subprocess.Popen([sys.executable, "-m", "pipeline.video_preview", str(src), str(copy)], cwd=REPO,
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            start_new_session=True)  # outlives the run, which ends without waiting for it
    tmp = job_path(copy).with_suffix(f".job.{os.getpid()}")
    tmp.write_text(str(proc.pid))
    os.replace(tmp, job_path(copy))
    return True


def main(argv: list[str]) -> int:
    src, copy = Path(argv[0]), Path(argv[1])
    try:
        convert(ffmpeg_argv(src, part_path(copy), is_hdr(src)), copy)
    finally:
        try:
            if job_path(copy).read_text().strip() == str(os.getpid()):
                job_path(copy).unlink()
        except OSError:
            pass  # not written yet, or already gone: a dead pid reads as not running anyway
    return 0 if copy.is_file() else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
