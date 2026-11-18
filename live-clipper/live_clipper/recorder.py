# pyright: reportMissingImports=false
"""Streamlink and FFmpeg recording engine for continuous live Twitch and YouTube capture."""

import os
import sys
import time
import shutil
import logging
import threading
import subprocess
from pathlib import Path
from typing import Optional, Callable, Dict, Any

# Ensure virtual environment packages are discoverable
_venv_site = Path(__file__).resolve().parent.parent / ".venv" / "lib" / "python3.11" / "site-packages"
if _venv_site.exists() and str(_venv_site) not in sys.path:
    sys.path.insert(0, str(_venv_site))

try:
    import streamlink  # type: ignore
except ImportError:
    streamlink = None

from live_clipper.config import ClipperConfig
from live_clipper.buffer_manager import BufferManager, Chunk

logger = logging.getLogger("live_clipper.recorder")


class RecordingEngine:
    """
    Records continuous live video (Twitch or YouTube) in 10-second chunks using Streamlink and FFmpeg.
    Ensures decoupled execution, automatic reconnection, and retention management.
    """
    def __init__(
        self,
        config: ClipperConfig,
        buffer_manager: BufferManager,
        on_chunk_completed: Optional[Callable[[Chunk], None]] = None
    ):
        self.config = config
        self.buffer_manager = buffer_manager
        self.on_chunk_completed = on_chunk_completed
        
        self.status = "idle" # idle, checking, offline, recording, reconnecting, stopped
        self.selected_quality = "none"
        self.last_chunk_time = 0.0
        self.last_error: Optional[str] = None
        self.total_chunks_recorded = 0
        
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._proc_streamlink: Optional[subprocess.Popen] = None
        self._proc_ffmpeg: Optional[subprocess.Popen] = None
        
        # Determine streamlink binary path
        venv_streamlink = self.config.base_dir / ".venv" / "bin" / "streamlink"
        if venv_streamlink.exists():
            self.streamlink_bin = str(venv_streamlink)
        else:
            self.streamlink_bin = shutil.which("streamlink") or "streamlink"

    def start(self):
        """Start recording thread."""
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, name="RecordingEngineThread", daemon=True)
        self._thread.start()
        logger.info(f"Recording engine started for {self.config.platform.upper()} stream: {self.config.stream_url}")

    def stop(self):
        """Stop recording and cleanup processes."""
        self.status = "stopped"
        self._stop_event.set()
        self._terminate_subprocesses()
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("Recording engine stopped")

    def _terminate_subprocesses(self):
        for proc in [self._proc_ffmpeg, self._proc_streamlink]:
            if proc and proc.poll() is None:
                try:
                    proc.terminate()
                    proc.wait(timeout=2)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass
        self._proc_streamlink = None
        self._proc_ffmpeg = None

    def check_stream_online(self) -> tuple[bool, Optional[str], Optional[str]]:
        """
        Check if channel or YouTube live stream is active and select optimal quality.
        Returns: (is_online, stream_name, stream_url)
        """
        try:
            if streamlink is not None:
                session = streamlink.Streamlink()
                if self.config.platform == "twitch":
                    session.set_option("twitch-disable-ads", True)
                    session.set_option("twitch-low-latency", True)
                
                streams = session.streams(self.config.stream_url)
                if not streams:
                    return False, None, None

                target_qualities = ["720p", "720p60", "best", "1080p60", "1080p", "480p", "worst"]
                chosen_quality = None
                for q in target_qualities:
                    if q in streams:
                        chosen_quality = q
                        break
                
                if not chosen_quality:
                    for k in streams.keys():
                        if "audio" not in k:
                            chosen_quality = k
                            break
                            
                if not chosen_quality:
                    chosen_quality = list(streams.keys())[0]

                return True, chosen_quality, streams[chosen_quality].to_url()
            else:
                # CLI fallback via streamlink --json
                cmd = [self.streamlink_bin, "--json", self.config.stream_url]
                res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=10)
                if res.returncode == 0:
                    import json
                    data = json.loads(res.stdout)
                    streams = data.get("streams", {})
                    if streams:
                        chosen = "720p" if "720p" in streams else ("best" if "best" in streams else list(streams.keys())[0])
                        return True, chosen, streams[chosen].get("url")
            return False, None, None
        except Exception as e:
            logger.debug(f"Stream online check failed: {e}")
            self.last_error = str(e)
            return False, None, None

    def _run_loop(self):
        """Outer supervisor loop for checking stream status and handling reconnections."""
        backoff_sec = 2
        while not self._stop_event.is_set():
            self.status = "checking"
            is_online, quality, stream_url = self.check_stream_online()
            
            if is_online and quality:
                self.status = "recording"
                self.selected_quality = quality
                backoff_sec = 2
                logger.info(f"Stream {self.config.stream_url} is LIVE! Recording quality: {quality}")
                
                try:
                    self._record_stream(quality)
                except Exception as e:
                    logger.error(f"Error during stream recording: {e}")
                    self.last_error = str(e)
                finally:
                    self._terminate_subprocesses()
                
                if self._stop_event.is_set():
                    break
                    
                self.status = "reconnecting"
                logger.info("Stream dropped or ended. Attempting reconnection...")
            else:
                self.status = "offline"
                self.selected_quality = "none"
                if not self.config.retry_offline:
                    logger.info(f"Stream {self.config.stream_url} is offline and retry_offline=False. Stopping supervisor.")
                    break
                logger.debug(f"Stream offline. Retrying in {backoff_sec} seconds...")
                time.sleep(backoff_sec)
                backoff_sec = min(backoff_sec * 2, 30)

    def _record_stream(self, quality: str):
        """Pipe Streamlink to FFmpeg segmenter to output 10-second timestamped chunks."""
        csv_list_path = self.config.temp_dir / f"segments_{int(time.time())}.csv"
        chunk_pattern = str(self.config.temp_dir / "chunk_%s.mp4")

        # Command for Streamlink
        streamlink_cmd = [
            self.streamlink_bin,
            "--stdout",
        ]
        if self.config.platform == "twitch":
            streamlink_cmd.append("--twitch-disable-ads")
            
        streamlink_cmd.extend([
            self.config.stream_url,
            quality or "720p,720p60,best"
        ])

        # Command for FFmpeg segmenter
        ffmpeg_cmd = [
            "ffmpeg", "-y", "-v", "warning",
            "-i", "pipe:0",
            "-c", "copy",
            "-f", "segment",
            "-segment_time", str(self.config.chunk_duration_sec),
            "-reset_timestamps", "1",
            "-strftime", "1",
            "-segment_list", str(csv_list_path),
            "-segment_list_type", "csv",
            chunk_pattern
        ]

        logger.info(f"Starting Streamlink: {' '.join(streamlink_cmd)}")
        self._proc_streamlink = subprocess.Popen(
            streamlink_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )

        logger.info(f"Starting FFmpeg: {' '.join(ffmpeg_cmd)}")
        self._proc_ffmpeg = subprocess.Popen(
            ffmpeg_cmd,
            stdin=self._proc_streamlink.stdout,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        if self._proc_streamlink.stdout:
            self._proc_streamlink.stdout.close()

        processed_files = set()
        file_seek_pos = 0

        while not self._stop_event.is_set():
            if self._proc_ffmpeg.poll() is not None:
                err = self._proc_ffmpeg.stderr.read().decode(errors="ignore") if self._proc_ffmpeg.stderr else ""
                logger.warning(f"FFmpeg process terminated (code {self._proc_ffmpeg.returncode}): {err}")
                break
            if self._proc_streamlink.poll() is not None:
                err = self._proc_streamlink.stderr.read().decode(errors="ignore") if self._proc_streamlink.stderr else ""
                logger.warning(f"Streamlink process terminated (code {self._proc_streamlink.returncode}): {err}")
                break

            if csv_list_path.exists():
                try:
                    with open(csv_list_path, "r", encoding="utf-8") as f:
                        f.seek(file_seek_pos)
                        new_lines = f.readlines()
                        file_seek_pos = f.tell()

                    for line in new_lines:
                        line = line.strip()
                        if not line:
                            continue
                        parts = line.split(",")
                        filename = parts[0].strip()
                        duration = float(parts[2].strip()) - float(parts[1].strip()) if len(parts) >= 3 else float(self.config.chunk_duration_sec)
                        
                        chunk_path = self.config.temp_dir / filename
                        if filename not in processed_files and chunk_path.exists():
                            processed_files.add(filename)
                            self.total_chunks_recorded += 1
                            self.last_chunk_time = time.time()
                            
                            chunk = self.buffer_manager.register_chunk(
                                chunk_path=chunk_path,
                                duration=duration
                            )
                            self.buffer_manager.cleanup_expired()

                            if self.on_chunk_completed:
                                try:
                                    self.on_chunk_completed(chunk)
                                except Exception as cb_err:
                                    logger.error(f"Error in on_chunk_completed callback: {cb_err}")

                except Exception as csv_err:
                    logger.debug(f"Error reading segments CSV: {csv_err}")

            time.sleep(1)

        if csv_list_path.exists():
            try:
                csv_list_path.unlink()
            except OSError:
                pass
