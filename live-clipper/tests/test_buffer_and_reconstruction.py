"""Unit tests for buffer retention, chunk locking, and reconstruction."""

import os
import time
import unittest
import subprocess
from pathlib import Path
from live_clipper.buffer_manager import BufferManager, Chunk


class TestBufferManager(unittest.TestCase):
    def setUp(self):
        self.test_dir = Path("live-clipper/temp/test_buffer")
        self.test_dir.mkdir(parents=True, exist_ok=True)
        self.bm = BufferManager(self.test_dir, retention_sec=600)

    def tearDown(self):
        for f in self.test_dir.glob("*"):
            try:
                f.unlink()
            except OSError:
                pass
        try:
            self.test_dir.rmdir()
        except OSError:
            pass

    def test_chunk_retention_and_expiration(self):
        t0 = 1000.0
        # Create dummy chunk files
        f1 = self.test_dir / "chunk_1.mp4"
        f2 = self.test_dir / "chunk_2.mp4"
        f1.touch()
        f2.touch()

        # Chunk 1: old (700 seconds ago -> expired)
        self.bm.register_chunk(f1, start_time=t0 - 700.0, duration=10.0)
        # Chunk 2: recent (100 seconds ago -> active)
        self.bm.register_chunk(f2, start_time=t0 - 100.0, duration=10.0)

        self.assertEqual(self.bm.get_active_chunk_count(), 2)

        # Run cleanup at t0
        deleted = self.bm.cleanup_expired(current_time=t0)
        self.assertEqual(deleted, 1)
        self.assertFalse(f1.exists())
        self.assertTrue(f2.exists())
        self.assertEqual(self.bm.get_active_chunk_count(), 1)

    def test_locked_chunks_never_deleted_even_when_expired(self):
        t0 = 1000.0
        f_locked = self.test_dir / "chunk_locked.mp4"
        f_locked.touch()

        chunk = self.bm.register_chunk(f_locked, start_time=t0 - 900.0, duration=10.0)
        
        # Lock chunk
        locked = self.bm.lock_chunks_for_range(t0 - 905.0, t0 - 895.0)
        self.assertEqual(len(locked), 1)
        self.assertGreater(locked[0].lock_count, 0)

        # Run cleanup
        deleted = self.bm.cleanup_expired(current_time=t0)
        self.assertEqual(deleted, 0)
        self.assertTrue(f_locked.exists())

        # Unlock and clean up
        self.bm.unlock_chunks(locked)
        deleted_after_unlock = self.bm.cleanup_expired(current_time=t0)
        self.assertEqual(deleted_after_unlock, 1)
        self.assertFalse(f_locked.exists())

    def test_reconstruct_clip_stitching(self):
        # Generate two 2-second real test chunks using FFmpeg
        c1_path = self.test_dir / "chunk_test_1.mp4"
        c2_path = self.test_dir / "chunk_test_2.mp4"
        out_path = self.test_dir / "reconstructed_out.mp4"

        cmd1 = ["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=duration=2:size=320x240:rate=10", "-f", "lavfi", "-i", "sine=duration=2", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", str(c1_path)]
        cmd2 = ["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=duration=2:size=320x240:rate=10", "-f", "lavfi", "-i", "sine=duration=2", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", str(c2_path)]
        subprocess.run(cmd1, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
        subprocess.run(cmd2, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)

        chunk1 = Chunk(path=c1_path, start_time=100.0, duration=2.0)
        chunk2 = Chunk(path=c2_path, start_time=102.0, duration=2.0)

        # Reconstruct 3 seconds starting from 100.5
        success = self.bm.reconstruct_clip([chunk1, chunk2], target_start=100.5, target_duration=3.0, output_path=out_path)
        self.assertTrue(success)
        self.assertTrue(out_path.exists())
        self.assertGreater(out_path.stat().st_size, 0)


if __name__ == "__main__":
    unittest.main()

