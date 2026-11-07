"""The run starts the playable copy and never waits for it (spec 2026-11-07-preview-at-run-end-design, P13)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from pipeline import publishing_config, video_preview
from pipeline.nodes import youtube_upload
from pipeline.ui import preview

FIX = Path(__file__).parent / "fixtures" / "ui"
HEVC = FIX / "preview" / "hevc10.mp4"
H264 = FIX / "inbox" / "PXL_20261030_070000000.mp4"


def _codec(monkeypatch, codec):
    monkeypatch.setattr(video_preview.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0, stdout=f"{codec}\n"))


@pytest.mark.parametrize("codec,needed", [("hevc", True), ("h264", False), ("", False)])
def test_DT_P13_only_a_video_a_browser_may_not_decode_is_converted_up_front(monkeypatch, codec, needed):
    """[DT-P13] A failed probe converts nothing: the console still converts on demand."""
    _codec(monkeypatch, codec)
    assert video_preview.needs_copy(Path("/v.mp4")) is needed


def test_needs_copy_survives_a_missing_ffprobe(monkeypatch):
    def boom(*a, **k):
        raise FileNotFoundError("ffprobe")

    monkeypatch.setattr(video_preview.subprocess, "run", boom)
    assert video_preview.needs_copy(Path("/v.mp4")) is False


class _Popen:
    started: list = []

    def __init__(self, argv, **kwargs):
        self.pid = os.getpid()  # alive
        _Popen.started.append((argv, kwargs))


@pytest.fixture
def popen(monkeypatch):
    _Popen.started = []
    monkeypatch.setattr(video_preview.subprocess, "Popen", _Popen)
    return _Popen.started


def test_DT_P13_starting_detaches_and_records_the_job(tmp_path, monkeypatch, popen):
    """[DT-P13] Its own session: the run ends, the conversion goes on."""
    _codec(monkeypatch, "hevc")
    rd = tmp_path / "run" / "langgraph"
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    copy = video_preview.copy_path(rd, "youtube", "video")
    video_preview.error_path(copy).parent.mkdir(parents=True)
    video_preview.error_path(copy).write_text("an older failure")

    assert video_preview.start_in_background(rd, "youtube", "video", video) is True

    argv, kwargs = popen[0]
    assert argv[1:] == ["-m", "pipeline.video_preview", str(video), str(copy)]
    assert kwargs["start_new_session"] is True
    assert video_preview.job_path(copy).read_text() == str(os.getpid())
    assert video_preview.job_running(copy)
    assert not video_preview.error_path(copy).exists()
    assert preview.state(rd, "youtube", "video") == {"status": "converting", "error": None}
    assert preview.converting(rd)


@pytest.mark.parametrize("why", ["copy", "job", "no file", "h264"])
def test_DT_P13_nothing_starts_when_there_is_nothing_to_do(tmp_path, monkeypatch, popen, why):
    """[DT-P13] A replayed node finds the copy or the job already there."""
    _codec(monkeypatch, "h264" if why == "h264" else "hevc")
    rd = tmp_path / "run" / "langgraph"
    video = tmp_path / "v.mp4"
    if why != "no file":
        video.write_bytes(b"x")
    copy = video_preview.copy_path(rd, "youtube", "video")
    copy.parent.mkdir(parents=True)
    if why == "copy":
        copy.write_bytes(b"done")
    if why == "job":
        video_preview.job_path(copy).write_text(str(os.getpid()))
    assert video_preview.start_in_background(rd, "youtube", "video", video) is False
    assert popen == []


def test_a_job_whose_process_is_gone_is_not_running(tmp_path):
    copy = tmp_path / "youtube-video.mp4"
    assert not video_preview.job_running(copy)
    video_preview.job_path(copy).write_text("not a pid")
    assert not video_preview.job_running(copy)
    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait()
    video_preview.job_path(copy).write_text(str(dead.pid))
    assert not video_preview.job_running(copy)
    video_preview.job_path(copy).write_text("0")
    assert not video_preview.job_running(copy)


def test_the_console_does_not_convert_what_a_run_is_converting(tmp_path, monkeypatch):
    rd = tmp_path / "run" / "langgraph"
    copy = video_preview.copy_path(rd, "youtube", "video")
    copy.parent.mkdir(parents=True)
    video_preview.job_path(copy).write_text(str(os.getpid()))
    threads = []
    monkeypatch.setattr(preview.threading, "Thread", lambda *a, **k: threads.append(k) or SimpleNamespace(start=lambda: None))
    assert preview.start(SimpleNamespace(runner="real"), rd, "youtube", "video", tmp_path / "v.mp4")["status"] == "converting"
    assert threads == []


def test_the_child_puts_the_copy_in_place_and_clears_its_job(tmp_path, monkeypatch):
    copy = tmp_path / "console-preview" / "youtube-video.mp4"
    copy.parent.mkdir()
    video_preview.job_path(copy).write_text(str(os.getpid()))

    def run(argv, **_):
        if argv[0] == "ffprobe":
            return SimpleNamespace(returncode=0, stdout="bt709\n")
        Path(argv[-1]).write_bytes(b"converted")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(video_preview.subprocess, "run", run)
    assert video_preview.main([str(tmp_path / "v.mp4"), str(copy)]) == 0
    assert copy.read_bytes() == b"converted"
    assert not video_preview.job_path(copy).exists()


def test_the_child_records_why_it_failed_and_keeps_a_job_not_its_own(tmp_path, monkeypatch):
    copy = tmp_path / "console-preview" / "youtube-video.mp4"
    copy.parent.mkdir()
    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait()
    video_preview.job_path(copy).write_text(str(dead.pid))
    monkeypatch.setattr(video_preview.subprocess, "run",
                        lambda argv, **_: SimpleNamespace(returncode=1, stdout="", stderr="Invalid data found"))
    assert video_preview.main([str(tmp_path / "v.mp4"), str(copy)]) == 1
    assert video_preview.error_path(copy).read_text() == "Invalid data found"
    assert video_preview.job_path(copy).read_text() == str(dead.pid)
    assert preview.state(tmp_path / "langgraph", "youtube", "video")["status"] == "failed"


# --- the node ----------------------------------------------------------------------------------------------------------


@pytest.fixture
def node_state(tmp_path, monkeypatch) -> dict:
    cfg = json.loads(publishing_config.CONFIG_PATH.read_text())
    path = tmp_path / "publishing.json"
    path.write_text(json.dumps(cfg))
    monkeypatch.setattr(publishing_config, "CONFIG_PATH", path)
    monkeypatch.setenv("PIPELINE_PROFILE", "prod")
    monkeypatch.setattr(youtube_upload, "get_duration_seconds", lambda _: 42.0)
    video = tmp_path / "v.mp4"
    video.write_bytes(b"\0")
    rd = tmp_path / "rd"
    (rd / "youtube-upload").mkdir(parents=True)
    meta = rd / "youtube-upload" / "metadata.json"
    meta.write_text(json.dumps({"title": "T", "description": "D", "tags": ["a"]}))
    return {"run_dir": str(rd), "video_path": str(video), "video_date": "2026-11-03", "extraction_report": "R",
            "blog_post_title": "B", "blog_post_text": "body", "youtube_metadata_path": str(meta)}


def test_DT_P13_the_node_starts_the_copy_with_the_package_and_returns_at_once(node_state, monkeypatch):
    """[DT-P13]"""
    seen = []
    monkeypatch.setattr(video_preview, "start_in_background", lambda *a: seen.append(a) or True)
    out = youtube_upload.node_youtube_upload(node_state)
    assert out["youtube_skip_reason"] == "manual"
    assert seen == [(Path(node_state["run_dir"]), "youtube", "video", Path(node_state["video_path"]))]


def test_DT_P13_a_preview_that_cannot_start_never_fails_the_node(node_state, monkeypatch):
    """[DT-P13]"""
    monkeypatch.setattr(video_preview, "start_in_background", lambda *a: (_ for _ in ()).throw(OSError("no disk")))
    assert youtube_upload.node_youtube_upload(node_state)["youtube_skip_reason"] == "manual"


# --- real processes ----------------------------------------------------------------------------------------------------


def _wait_for(copy: Path) -> None:
    deadline = time.time() + 120
    while time.time() < deadline and (video_preview.job_running(copy) or not (copy.is_file() or video_preview.error_path(copy).is_file())):
        time.sleep(0.1)


@pytest.mark.integration
def test_DT_P13_a_real_detached_conversion_of_a_phone_video(tmp_path):
    """[DT-P13] Real ffprobe and ffmpeg, in the process the run leaves behind."""
    rd = tmp_path / "run" / "langgraph"
    t0 = time.time()
    assert video_preview.start_in_background(rd, "youtube", "video", HEVC) is True
    assert time.time() - t0 < 5  # the caller does not wait for ffmpeg
    copy = video_preview.copy_path(rd, "youtube", "video")
    _wait_for(copy)
    assert copy.is_file(), video_preview.error_path(copy).read_text() if video_preview.error_path(copy).exists() else "no copy"
    codec = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=codec_name",
                            "-of", "default=nw=1:nk=1", str(copy)], capture_output=True, text=True).stdout.strip()
    assert codec == "h264"
    assert not video_preview.job_path(copy).exists()
    assert not video_preview.part_path(copy).exists()


@pytest.mark.integration
def test_DT_P13_a_real_h264_video_is_left_alone(tmp_path):
    """[DT-P13]"""
    assert video_preview.start_in_background(tmp_path / "run" / "langgraph", "youtube", "video", H264) is False
    assert not (tmp_path / "run" / "console-preview").exists()
