"""A browser-playable copy of a package's video (spec 2026-11-05-console-end-to-end section 2 P12).

Unit tests stub the subprocess. Those marked integration run a real one, real ffmpeg included.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from pipeline import publishing_config
from pipeline.publish import package
from pipeline.ui import artifacts, preview
from pipeline.ui.app import create_app
from pipeline.ui.settings import Settings

FIX = Path(__file__).parent / "fixtures" / "ui"
ORIGINAL = FIX / "inbox" / "PXL_20261030_070000000.mp4"
ASSET = "/api/runs/fx-unpackaged/publish/youtube/asset/video"
PREVIEW = f"{ASSET}/preview"


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    from pipeline import graph

    monkeypatch.setenv("CHECKPOINT_DB_PATH", str(tmp_path / "ck.db"))
    monkeypatch.setattr(graph, "CHECKPOINT_DB", tmp_path / "ck.db")
    monkeypatch.setattr(publishing_config, "CONFIG_PATH", FIX / "publishing.json")


@pytest.fixture
def processed(tmp_path) -> Path:
    shutil.copytree(FIX / "processed", tmp_path / "processed")
    return tmp_path / "processed"


def client_for(processed: Path, runner: str = "real") -> TestClient:
    settings = Settings(processed_root=processed, inbox=FIX / "inbox", runner=runner, books_root=FIX / "books")
    client = TestClient(create_app(settings), base_url="http://127.0.0.1")
    assert client.post("/api/runs/fx-unpackaged/publish/youtube/prepare").status_code == 201
    return client


@pytest.fixture
def client(processed) -> TestClient:
    return client_for(processed)


def settled(client: TestClient) -> dict:
    deadline = time.time() + 60
    while time.time() < deadline:
        state = client.get(PREVIEW).json()
        if state["status"] != "converting":
            return state
        time.sleep(0.02)
    raise AssertionError("the conversion never finished")


def fake_ffmpeg(monkeypatch, *, rc: int = 0, stderr: str = "", raises=None, gate: threading.Event | None = None) -> list:
    """Stands in for subprocess.run: ffprobe answers SDR, ffmpeg writes its last argument."""
    calls: list = []

    def run(argv, **_):
        if "ffprobe" in argv[0]:
            return SimpleNamespace(returncode=0, stdout="bt709\n", stderr="")
        calls.append(argv)
        if gate:
            assert gate.wait(10)
        if raises:
            raise raises
        if rc == 0:
            Path(argv[-1]).write_bytes(b"converted")
        return SimpleNamespace(returncode=rc, stdout="", stderr=stderr)

    monkeypatch.setattr(preview.subprocess, "run", run)
    return calls


def test_a_plain_video_is_scaled_and_encoded_for_any_browser():  # [DT-P12]
    argv = preview.ffmpeg_argv(Path("/in.mp4"), Path("/out.mp4"), hdr=False)
    joined = " ".join(argv)
    assert argv[argv.index("-i") + 1] == "/in.mp4" and argv[-1] == "/out.mp4"
    assert "libx264" in argv and "aac" in argv and "+faststart" in argv
    assert "scale=1280:1280:force_original_aspect_ratio=decrease" in joined and "format=yuv420p" in joined
    assert "tonemap" not in joined
    assert "0:a:0?" in argv  # a video without audio still converts


def test_an_hdr_video_is_tone_mapped_or_it_would_look_washed_out():  # [DT-P12]
    assert "tonemap=hable" in " ".join(preview.ffmpeg_argv(Path("/in.mp4"), Path("/out.mp4"), hdr=True))


@pytest.mark.parametrize("stdout,expected", [("arib-std-b67\n", True), ("smpte2084\n", True), ("bt709\n", False), ("\n", False)])
def test_hdr_is_read_from_the_transfer(monkeypatch, stdout, expected):  # [DT-P12]
    monkeypatch.setattr(preview.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0, stdout=stdout))
    assert preview.is_hdr(Path("/in.mp4")) is expected


def test_a_failed_probe_converts_as_plain_and_lets_ffmpeg_report(monkeypatch):  # [DT-P12]
    def boom(*a, **k):
        raise FileNotFoundError("ffprobe")

    monkeypatch.setattr(preview.subprocess, "run", boom)
    assert preview.is_hdr(Path("/in.mp4")) is False


def test_nothing_has_been_asked_for_yet(client):  # [DT-P12]
    assert client.get(PREVIEW).json() == {"status": "none", "error": None}
    assert client.get(f"{PREVIEW}/file").status_code == 404


def test_convert_then_serve_the_copy_and_leave_the_original_alone(client, processed, monkeypatch):  # [DT-P12]
    calls = fake_ffmpeg(monkeypatch)
    before = client.get("/api/runs/fx-unpackaged/publish").json()["targets"]["youtube"]["assets"]
    assert client.post(PREVIEW).status_code == 202
    assert settled(client) == {"status": "ready", "error": None}
    assert len(calls) == 1 and calls[0][calls[0].index("-i") + 1] == str(ORIGINAL)

    r = client.get(f"{PREVIEW}/file")
    assert r.status_code == 200 and r.headers["content-type"] == "video/mp4" and r.content == b"converted"
    r = client.get(f"{PREVIEW}/file", headers={"Range": "bytes=0-0"})
    assert r.status_code == 206 and r.content == b"c"

    # What gets uploaded is still the phone's file, and the copy never shows up among the run's files.
    assert client.get("/api/runs/fx-unpackaged/publish").json()["targets"]["youtube"]["assets"] == before
    assert client.get(ASSET).content == ORIGINAL.read_bytes()
    rd = processed / "fx-unpackaged" / "langgraph"
    assert package.read(rd, "youtube")["assets"] == before
    assert not [a for a in artifacts.list_artifacts(rd) if "preview" in str(a["path"])]


def test_a_ready_copy_is_not_converted_again(client, monkeypatch):  # [DT-P12]
    calls = fake_ffmpeg(monkeypatch)
    client.post(PREVIEW)
    settled(client)
    r = client.post(PREVIEW)
    assert r.status_code == 202 and r.json()["status"] == "ready"
    assert len(calls) == 1


def test_asking_twice_while_converting_runs_one_conversion(client, monkeypatch):  # [DT-P12]
    gate = threading.Event()
    calls = fake_ffmpeg(monkeypatch, gate=gate)
    assert client.post(PREVIEW).json()["status"] == "converting"
    assert client.post(PREVIEW).json()["status"] == "converting"
    assert client.get(PREVIEW).json()["status"] == "converting"
    gate.set()
    assert settled(client)["status"] == "ready"
    assert len(calls) == 1


@pytest.mark.parametrize("kwargs,reason", [
    ({"rc": 1, "stderr": "Invalid data found"}, "Invalid data found"),
    ({"rc": 1, "stderr": ""}, "ffmpeg exited with 1"),
    ({"raises": FileNotFoundError("no ffmpeg")}, "no ffmpeg"),
    ({"raises": subprocess.TimeoutExpired("ffmpeg", 1800)}, "timed out"),
])
def test_a_failure_says_why_and_asking_again_retries(client, monkeypatch, kwargs, reason):  # [DT-P12]
    fake_ffmpeg(monkeypatch, **kwargs)
    client.post(PREVIEW)
    state = settled(client)
    assert state["status"] == "failed" and reason in state["error"]
    assert client.get(f"{PREVIEW}/file").status_code == 404

    fake_ffmpeg(monkeypatch)
    assert client.post(PREVIEW).status_code == 202
    assert settled(client) == {"status": "ready", "error": None}


def test_a_long_ffmpeg_error_is_cut_to_its_end(client, monkeypatch):  # [DT-P12]
    fake_ffmpeg(monkeypatch, rc=1, stderr="x" * 5000 + "the cause")
    client.post(PREVIEW)
    error = settled(client)["error"]
    assert error.endswith("the cause") and len(error) <= preview.ERROR_CHARS


@pytest.mark.parametrize("url", [
    "/api/runs/nope/publish/youtube/asset/video/preview",
    "/api/runs/fx-unpackaged/publish/nope/asset/video/preview",
    "/api/runs/fx-unpackaged/publish/youtube/asset/nope/preview",
    "/api/runs/fx-unpackaged/publish/wordpress/asset/featured_image/preview",  # only a video is ever converted
    "/api/runs/fx-done/publish/youtube/asset/video/preview",  # no such asset in this package
])
def test_only_a_listed_video_can_be_converted(client, url):  # [DT-P12]
    assert client.get(url).status_code == 404
    assert client.post(url).status_code == 404
    assert client.get(f"{url}/file").status_code == 404


def test_a_video_whose_file_is_gone_cannot_be_converted(client, processed):  # [DT-P12]
    rd = processed / "fx-unpackaged" / "langgraph"
    pkg = package.read(rd, "youtube")
    package.write(rd, {**pkg, "assets": [{"role": "video", "path": "/nope/gone.mp4"}]})
    assert client.post(PREVIEW).status_code == 404


# --- real subprocess ---------------------------------------------------------------


def _probe(path: Path) -> dict:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "stream=codec_type,codec_name,pix_fmt,width,height,color_transfer", "-of", "json", str(path)],
        capture_output=True, text=True, check=True).stdout
    return {s["codec_type"]: s for s in json.loads(out)["streams"]}


def _phone_video(tmp_path: Path) -> Path:
    """What a Pixel records: HEVC Main 10, HLG, with its rotation in the container."""
    raw, phone = tmp_path / "raw.mp4", tmp_path / "phone.mp4"
    subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=s=1600x200:d=0.3:r=10",
         "-f", "lavfi", "-i", "sine=d=0.3", "-c:v", "libx265", "-preset", "ultrafast", "-pix_fmt", "yuv420p10le",
         "-x265-params", "log-level=error:colorprim=bt2020:transfer=arib-std-b67:colormatrix=bt2020nc",
         "-color_primaries", "bt2020", "-color_trc", "arib-std-b67", "-colorspace", "bt2020nc",
         "-tag:v", "hvc1", "-c:a", "aac", "-shortest", str(raw)], check=True)
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-display_rotation", "90", "-i", str(raw), "-c", "copy",
                    str(phone)], check=True)
    return phone


def _use_source(processed: Path, source: Path) -> None:
    rd = processed / "fx-unpackaged" / "langgraph"
    package.write(rd, {**package.read(rd, "youtube"), "assets": [{"role": "video", "path": str(source)}]})


@pytest.mark.integration
def test_real_ffmpeg_turns_a_phone_video_into_what_chrome_plays(client, processed, tmp_path):  # [DT-P12]
    phone = _phone_video(tmp_path)
    assert _probe(phone)["video"]["codec_name"] == "hevc"
    _use_source(processed, phone)
    assert client.post(PREVIEW).status_code == 202
    assert settled(client) == {"status": "ready", "error": None}

    copy = tmp_path / "copy.mp4"
    copy.write_bytes(client.get(f"{PREVIEW}/file").content)
    streams = _probe(copy)
    video = streams["video"]
    assert video["codec_name"] == "h264" and video["pix_fmt"] == "yuv420p" and video["color_transfer"] == "bt709"
    assert (video["width"], video["height"]) == (160, 1280)  # upright, long side capped
    assert streams["audio"]["codec_name"] == "aac"
    assert client.get(ASSET).content == phone.read_bytes()


@pytest.mark.integration
def test_real_ffmpeg_on_a_broken_file_reports_its_reason(client, processed, tmp_path):  # [DT-P12]
    broken = tmp_path / "broken.mp4"
    broken.write_text("not a video")
    _use_source(processed, broken)
    client.post(PREVIEW)
    state = settled(client)
    assert state["status"] == "failed" and state["error"]
    assert not list((processed / "fx-unpackaged" / "console-preview").glob("*.part.mp4"))


@pytest.mark.integration
def test_the_fake_runner_converts_without_ffmpeg(processed):  # [DT-P12]
    client = client_for(processed, runner="fake")
    client.post(PREVIEW)
    assert settled(client)["status"] == "ready"
    assert client.get(f"{PREVIEW}/file").content == ORIGINAL.read_bytes()


@pytest.mark.integration
def test_a_foreign_page_cannot_start_a_conversion(client):  # [DT-P12]
    assert client.post(PREVIEW, headers={"Origin": "https://evil.example"}).status_code == 403
    assert client.get(PREVIEW).json()["status"] == "none"
