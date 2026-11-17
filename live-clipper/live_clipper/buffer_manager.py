"""Buffer Manager for rolling chunk storage and lifecycle."""

import os
import time
import logging
import threading
import subprocess
from pathlib import Path
from typing import List, Dict, Optional, Set
from dataclasses import dataclass

logger = logging.getLogger("live_clipper.buffer_manager")

@dataclass
class Chunk:
    path: Path
    start_time: float
    duration: float
    lock_count: int = 0

    @property
    def end_time(self) -> float:
        return self.start_time + self.duration


class BufferManager:
    """
    Manages 10-second video chunks with a 10-minute rolling buffer.
    Ensures safe deletion of expired chunks while protecting chunks currently being processed.
    """
    def __init__(self, temp_dir: Path, retention_sec: int = 600):
        self.temp_dir = Path(temp_dir)
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        self.retention_sec = retention_sec
        self._lock = threading.RLock()
        self.chunks: Dict[str, Chunk] = {} # filename -> Chunk

    def register_chunk(self, chunk_path: Path, start_time: Optional[float] = None, duration: float = 10.0) -> Chunk:
        """Register a newly completed chunk in the buffer."""
        chunk_path = Path(chunk_path)
        with self._lock:
            # If start_time not provided, parse from filename or modification time
            if start_time is None:
                start_time = self._extract_timestamp_from_filename(chunk_path.name) or time.time()
                
            chunk = Chunk(path=chunk_path, start_time=start_time, duration=duration)
            self.chunks[chunk_path.name] = chunk
            logger.debug(f"Registered chunk {chunk_path.name} (start={start_time:.2f}, dur={duration}s)")
            return chunk

    def _extract_timestamp_from_filename(self, filename: str) -> Optional[float]:
        """Attempt to extract epoch timestamp from filename pattern chunk_TIMESTAMP.ext."""
        try:
            name = filename.rsplit(".", 1)[0]
            parts = name.split("_")
            if len(parts) >= 2:
                # Check for epoch float/int: chunk_1700000000.123
                return float(parts[1])
        except (ValueError, IndexError):
            pass
        return None

    def lock_chunks_for_range(self, start_time: float, end_time: float) -> List[Chunk]:
        """
        Find all chunks that overlap with [start_time, end_time] and lock them so they cannot be deleted.
        """
        with self._lock:
            selected: List[Chunk] = []
            for chunk in self.chunks.values():
                # Overlap test: chunk.start_time < end_time and chunk.end_time > start_time
                if chunk.start_time <= end_time and chunk.end_time >= start_time:
                    chunk.lock_count += 1
                    selected.append(chunk)
            # Sort chronologically
            selected.sort(key=lambda c: c.start_time)
            logger.info(f"Locked {len(selected)} chunks for range [{start_time:.1f}, {end_time:.1f}]")
            return selected

    def unlock_chunks(self, chunks: List[Chunk]):
        """Release processing lock on the given chunks."""
        with self._lock:
            for chunk in chunks:
                if chunk.path.name in self.chunks:
                    target = self.chunks[chunk.path.name]
                    target.lock_count = max(0, target.lock_count - 1)
            logger.debug(f"Unlocked {len(chunks)} chunks")

    def cleanup_expired(self, current_time: Optional[float] = None) -> int:
        """
        Delete chunks older than retention_sec unless they are currently locked.
        Returns the number of deleted chunks.
        """
        now = current_time or time.time()
        cutoff_time = now - self.retention_sec
        deleted_count = 0

        with self._lock:
            to_delete = []
            for name, chunk in self.chunks.items():
                # Expired if end_time is before cutoff
                if chunk.end_time < cutoff_time:
                    if chunk.lock_count > 0:
                        logger.debug(f"Preserving expired chunk {name} because it is locked (lock_count={chunk.lock_count})")
                    else:
                        to_delete.append(name)

            for name in to_delete:
                chunk = self.chunks.pop(name)
                try:
                    if chunk.path.exists():
                        chunk.path.unlink()
                        deleted_count += 1
                        logger.debug(f"Deleted expired chunk {name}")
                except Exception as e:
                    logger.error(f"Failed to delete chunk {chunk.path}: {e}")

        return deleted_count

    def get_active_chunk_count(self) -> int:
        with self._lock:
            return len(self.chunks)

    def get_buffer_duration(self) -> float:
        """Calculate total duration spanning the current retained buffer."""
        with self._lock:
            if not self.chunks:
                return 0.0
            sorted_chunks = sorted(self.chunks.values(), key=lambda c: c.start_time)
            earliest = sorted_chunks[0].start_time
            latest = sorted_chunks[-1].end_time
            return max(0.0, latest - earliest)

    def get_latest_chunks(self, n: int = 1) -> List[Chunk]:
        """Return the n most recent chunks."""
        with self._lock:
            sorted_chunks = sorted(self.chunks.values(), key=lambda c: c.start_time)
            return sorted_chunks[-n:]

    def reconstruct_clip(self, chunks: List[Chunk], target_start: float, target_duration: float, output_path: Path) -> bool:
        """
        Stitch contiguous chunks together and accurately trim target_duration from target_start.
        """
        if not chunks:
            logger.error("No chunks provided for clip reconstruction")
            return False

        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        concat_list_file = self.temp_dir / f"concat_{int(time.time()*1000)}.txt"
        assembled_raw = self.temp_dir / f"raw_{int(time.time()*1000)}.mp4"

        try:
            # 1. Write concat demuxer list
            with open(concat_list_file, "w", encoding="utf-8") as f:
                for chunk in chunks:
                    # Escape single quotes if any
                    escaped = str(chunk.path.resolve()).replace("'", "'\\''")
                    f.write(f"file '{escaped}'\n")

            # 2. Concat files
            concat_cmd = [
                "ffmpeg", "-y", "-v", "error",
                "-f", "concat", "-safe", "0",
                "-i", str(concat_list_file),
                "-c", "copy",
                str(assembled_raw)
            ]
            res = subprocess.run(concat_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
            if res.returncode != 0:
                logger.error(f"FFmpeg concat failed: {res.stderr.decode()}")
                return False

            # 3. Calculate exact start offset relative to first chunk
            first_chunk_start = chunks[0].start_time
            offset = max(0.0, target_start - first_chunk_start)

            trim_cmd = [
                "ffmpeg", "-y", "-v", "error",
                "-ss", f"{offset:.3f}",
                "-i", str(assembled_raw),
                "-t", f"{target_duration:.3f}",
                "-c", "copy",
                str(output_path)
            ]
            res2 = subprocess.run(trim_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
            if res2.returncode != 0:
                # If stream copy trim fails, re-encode copy fallback
                trim_cmd_fallback = [
                    "ffmpeg", "-y", "-v", "error",
                    "-ss", f"{offset:.3f}",
                    "-i", str(assembled_raw),
                    "-t", f"{target_duration:.3f}",
                    "-c:v", "libx264", "-preset", "ultrafast",
                    "-c:a", "aac",
                    str(output_path)
                ]
                res_fb = subprocess.run(trim_cmd_fallback, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
                if res_fb.returncode != 0:
                    logger.error(f"FFmpeg trim failed: {res_fb.stderr.decode()}")
                    return False

            return output_path.exists() and output_path.stat().st_size > 0

        except Exception as e:
            logger.error(f"Exception during clip reconstruction: {e}")
            return False
        finally:
            if concat_list_file.exists():
                try:
                    concat_list_file.unlink()
                except OSError:
                    pass
            if assembled_raw.exists():
                try:
                    assembled_raw.unlink()
                except OSError:
                    pass

