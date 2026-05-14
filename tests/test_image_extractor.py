"""Tests for the smart-frame image extractor.

The heavy path (ffmpeg + OpenCV) is only exercised when the binaries +
packages are installed locally. The pure-Python helpers are always tested.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.image_extractor import (  # noqa: E402
    ImageExtractionError,
    sample_candidate_timestamps,
)


class SampleTimestampsTest(unittest.TestCase):
    def test_zero_duration_returns_empty(self) -> None:
        self.assertEqual(sample_candidate_timestamps(0.0), [])

    def test_band_is_centered_on_midpoint(self) -> None:
        ts = sample_candidate_timestamps(100.0, samples=11, center=0.5, spread=0.2)
        self.assertEqual(len(ts), 11)
        self.assertAlmostEqual(min(ts), 30.0, places=1)
        self.assertAlmostEqual(max(ts), 70.0, places=1)

    def test_single_sample_returns_midpoint(self) -> None:
        ts = sample_candidate_timestamps(10.0, samples=1)
        self.assertEqual(len(ts), 1)
        self.assertGreater(ts[0], 0)
        self.assertLess(ts[0], 10)


def _ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def _opencv_available() -> bool:
    try:
        import cv2  # noqa: F401
        import numpy  # noqa: F401
        return True
    except ImportError:
        return False


@unittest.skipUnless(_ffmpeg_available() and _opencv_available(), "ffmpeg / opencv missing")
class EndToEndImageExtractTest(unittest.TestCase):
    def test_synthesised_video_produces_jpeg(self) -> None:
        from pipeline.image_extractor import pick_and_resize_best
        with TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            video_path = tmp_dir / "fixture.mp4"
            # 3-second 320x240 SMPTE bars at 24fps.
            subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-f", "lavfi",
                    "-i", "smptebars=size=320x240:rate=24:duration=3",
                    "-pix_fmt", "yuv420p",
                    str(video_path),
                ],
                check=True,
                capture_output=True,
            )
            out_path = tmp_dir / "featured.jpg"
            result_path, score, candidates = pick_and_resize_best(
                video_path, out_path, size=(640, 360), samples=5
            )
            self.assertTrue(result_path.is_file())
            self.assertGreater(score, 0)
            self.assertGreater(len(candidates), 0)
            header = result_path.read_bytes()[:3]
            self.assertEqual(header, b"\xff\xd8\xff")  # JPEG SOI marker

    def test_missing_video_raises(self) -> None:
        from pipeline.image_extractor import pick_and_resize_best
        with TemporaryDirectory() as tmp:
            with self.assertRaises((ImageExtractionError, subprocess.CalledProcessError)):
                pick_and_resize_best(Path(tmp) / "nope.mp4", Path(tmp) / "out.jpg")


if __name__ == "__main__":
    unittest.main(verbosity=2)
