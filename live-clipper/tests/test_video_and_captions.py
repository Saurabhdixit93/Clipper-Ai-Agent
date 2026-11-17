"""Unit tests for subtitle rendering, faster-whisper transcription, and vertical video rendering."""

import os
import unittest
import subprocess
from pathlib import Path
from PIL import Image

from live_clipper.config import ClipperConfig
from live_clipper.transcriber import Transcriber, SubtitleCue
from live_clipper.video_processor import VideoProcessor


class TestVideoAndCaptions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = ClipperConfig()
        cls.transcriber = Transcriber(cls.config)
        cls.processor = VideoProcessor(cls.config)
        cls.test_dir = Path("live-clipper/temp/test_vc")
        cls.test_dir.mkdir(parents=True, exist_ok=True)

    @classmethod
    def tearDownClass(cls):
        for f in cls.test_dir.glob("*"):
            try:
                f.unlink()
            except OSError:
                pass
        try:
            cls.test_dir.rmdir()
        except OSError:
            pass

    def test_subtitle_image_generation(self):
        sub_img = self.test_dir / "sub_test.png"
        ok = self.transcriber.render_subtitle_image("OMG WHAT A GOAL", sub_img)
        self.assertTrue(ok)
        self.assertTrue(sub_img.exists())
        
        # Verify image properties
        with Image.open(sub_img) as img:
            self.assertEqual(img.size, (1080, 1920))
            self.assertEqual(img.mode, "RGBA")

    def test_vertical_video_rendering_and_dimensions(self):
        # 1. Create a 2-second 16:9 test clip
        raw_clip = self.test_dir / "raw_16_9.mp4"
        cmd = [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "testsrc=duration=2:size=1280x720:rate=15",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
            "-c:v", "libx264", "-preset", "ultrafast",
            "-c:a", "aac",
            str(raw_clip)
        ]
        subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)

        # 2. Render vertical 1080x1920 with subtitles
        out_vertical = self.test_dir / "out_vertical.mp4"
        cues = [
            SubtitleCue(start_sec=0.2, end_sec=1.0, text="TESTING CAPTION 1"),
            SubtitleCue(start_sec=1.1, end_sec=1.8, text="TESTING CAPTION 2")
        ]
        ok = self.processor.render_vertical_clip(
            raw_clip_path=raw_clip,
            output_clip_path=out_vertical,
            subtitle_cues=cues,
            transcriber=self.transcriber
        )
        self.assertTrue(ok)
        self.assertTrue(out_vertical.exists())

        # 3. Check probe output: width=1080, height=1920
        probe_cmd = [
            "ffprobe", "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=width,height",
            "-of", "csv=s=x:p=0",
            str(out_vertical)
        ]
        res = subprocess.run(probe_cmd, stdout=subprocess.PIPE, text=True, check=True)
        dims = res.stdout.strip()
        self.assertEqual(dims, "1080x1920")


if __name__ == "__main__":
    unittest.main()

