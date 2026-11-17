"""End-to-end integration test with synthetic video, audio spikes, and chat simulation."""

import os
import time
import shutil
import unittest
import subprocess
from pathlib import Path

from live_clipper.config import ClipperConfig
from live_clipper.app import LiveClipperApp


class TestEndToEndSynthetic(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.test_base = Path("live-clipper/temp/test_e2e")
        cls.test_base.mkdir(parents=True, exist_ok=True)
        
        cls.config = ClipperConfig(
            channel="ishowspeed",
            base_dir=cls.test_base,
            clips_dir=cls.test_base / "clips",
            temp_dir=cls.test_base / "temp",
            warmup_duration_sec=10, # Short warmup for synthetic test
            cooldown_sec=30
        )
        cls.app = LiveClipperApp(cls.config)

    @classmethod
    def tearDownClass(cls):
        try:
            cls.app.stop()
        except Exception:
            pass
        if cls.test_base.exists():
            shutil.rmtree(cls.test_base, ignore_errors=True)

    def test_full_pipeline_from_spikes_to_clip_and_txt(self):
        # 1. Warm up chat monitor
        self.app.chat_monitor.is_connected = True
        self.app.chat_monitor.connection_start_time = time.time() - 20.0
        self.assertTrue(self.app.chat_monitor.is_warmed_up)

        # 2. Populate baseline chat messages (5 msgs per 5s bucket)
        now = time.time()
        t_base = now - 250.0
        for b_idx in range(50):
            t = t_base + (b_idx * 5.0)
            for m in range(5):
                self.app.chat_monitor._record_message(f"user_{m}", "speed stream hype", t)

        # Baseline chat rate should be ~5
        baseline_chat = self.app.chat_monitor.get_rolling_chat_average(exclude_current_spike=True)
        self.assertAlmostEqual(baseline_chat, 5.0, delta=1.0)

        # 3. Generate five 10-second video chunks
        chunks = []
        for i in range(5):
            chunk_start = now - 45.0 + (i * 10.0)
            chunk_file = self.config.temp_dir / f"chunk_{int(chunk_start)}.mp4"
            
            # Baseline volume: -25 dB; Chunk 3 volume: -5 dB (loud spike)
            audio_vol = "0.05" if i != 3 else "0.8"
            
            gen_cmd = [
                "ffmpeg", "-y", "-v", "error",
                "-f", "lavfi", "-i", "testsrc=duration=10:size=1280x720:rate=15",
                "-f", "lavfi", "-i", f"sine=frequency=440:duration=10",
                "-af", f"volume={audio_vol}",
                "-c:v", "libx264", "-preset", "ultrafast",
                "-c:a", "aac",
                str(chunk_file)
            ]
            subprocess.run(gen_cmd, check=True)
            chunk = self.app.buffer_manager.register_chunk(chunk_file, start_time=chunk_start, duration=10.0)
            chunks.append(chunk)

        # 4. Inject 4x chat spike at chunk 3 time
        spike_time = now - 14.0
        for m in range(25): # 25 msgs >= 5 * 3x
            self.app.chat_monitor._record_message(f"spike_user_{m}", "W W W SIUUU SPEED", spike_time)

        # 5. Process chunks through app pipeline
        for chunk in chunks:
            self.app._on_chunk_completed(chunk)

        # 6. Verify trigger fired
        self.assertEqual(self.app.trigger_engine.total_triggers_fired, 1)

        # 7. Start worker to execute the queued job
        self.app.worker.start()
        
        # Wait for worker to finish processing the clip
        timeout = 120
        start_wait = time.time()
        while self.app.worker.queue.qsize() > 0 or self.app.worker.is_busy:
            if time.time() - start_wait > timeout:
                self.fail("Timed out waiting for worker to process clip")
            time.sleep(1)

        self.app.worker.stop()

        # 8. Verify clip creation in clips directory
        created_mp4s = list(self.config.clips_dir.glob("*.mp4"))
        created_txts = list(self.config.clips_dir.glob("*.txt"))

        self.assertEqual(len(created_mp4s), 1, f"Expected 1 MP4 clip, found {len(created_mp4s)}")
        self.assertEqual(len(created_txts), 1, f"Expected 1 TXT metadata file, found {len(created_txts)}")

        mp4_path = created_mp4s[0]
        txt_path = created_txts[0]

        # Verify MP4 is valid and 1080x1920
        probe_cmd = [
            "ffprobe", "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=width,height",
            "-of", "csv=s=x:p=0",
            str(mp4_path)
        ]
        res = subprocess.run(probe_cmd, stdout=subprocess.PIPE, text=True, check=True)
        self.assertEqual(res.stdout.strip(), "1080x1920")

        # Verify TXT metadata content
        with open(txt_path, "r", encoding="utf-8") as f:
            txt_content = f.read()

        self.assertIn("Title:", txt_content)
        self.assertIn("Hashtags:", txt_content)
        self.assertIn("Event Timestamp:", txt_content)
        self.assertIn("Transcript:", txt_content)
        print("\n✅ Synthetic E2E Test Passed! Created:", mp4_path.name, "and", txt_path.name)


if __name__ == "__main__":
    unittest.main()
