"""Background worker queue and clip rendering pipeline for decoupled processing."""

import os
import re
import time
import queue
import logging
import threading
from pathlib import Path
from typing import Optional, List, Dict
from dataclasses import dataclass

from live_clipper.config import ClipperConfig
from live_clipper.buffer_manager import BufferManager, Chunk
from live_clipper.chat_monitor import TwitchChatMonitor
from live_clipper.trigger_engine import TriggerEvent
from live_clipper.transcriber import Transcriber
from live_clipper.video_processor import VideoProcessor
from live_clipper.title_generator import TitleGenerator

logger = logging.getLogger("live_clipper.worker")


def sanitize_filename(name: str) -> str:
    """
    Sanitize string for Windows and POSIX filesystem compatibility.
    Removes invalid characters: < > : " / \\ | ? * and controls.
    """
    # Replace whitespace and illegal characters with hyphens/underscores
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '', name)
    cleaned = re.sub(r'\s+', '-', cleaned)
    cleaned = re.sub(r'-+', '-', cleaned)
    cleaned = cleaned.strip('-._')
    return cleaned[:45] if cleaned else "clip"


@dataclass
class ClipJob:
    event: TriggerEvent
    locked_chunks: List[Chunk]


class ProcessingWorker:
    """
    Decoupled background worker for non-blocking clip reconstruction,
    transcription, subtitle generation, vertical rendering, and metadata export.
    """
    def __init__(
        self,
        config: ClipperConfig,
        buffer_manager: BufferManager,
        chat_monitor: TwitchChatMonitor,
        transcriber: Transcriber,
        video_processor: VideoProcessor,
        title_generator: TitleGenerator
    ):
        self.config = config
        self.buffer_manager = buffer_manager
        self.chat_monitor = chat_monitor
        self.transcriber = transcriber
        self.video_processor = video_processor
        self.title_generator = title_generator
        
        self.queue: queue.Queue[ClipJob] = queue.Queue(maxsize=10)
        self.total_clips_created = 0
        self.processing_errors = 0
        self.last_created_clip_name: Optional[str] = None
        self.is_busy = False

        # Real-time editing state for dashboard
        self.editing_status: str = "IDLE"
        self.editing_progress_pct: float = 0.0
        self.current_hook_text: str = ""

        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self):
        """Start worker thread."""
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._worker_loop, name="ClipWorkerThread", daemon=True)
        self._thread.start()
        logger.info("Background processing worker started")

    def stop(self):
        """Stop worker thread."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("Background processing worker stopped")

    def submit_job(self, event: TriggerEvent):
        """
        Locks the relevant chunks and puts the clip job into the bounded queue.
        Does not block recorder or chat monitor.
        """
        locked = self.buffer_manager.lock_chunks_for_range(
            event.clip_start_time,
            event.clip_end_time
        )
        job = ClipJob(event=event, locked_chunks=locked)
        try:
            self.queue.put_nowait(job)
            logger.info(f"Queued clip job for event at {event.event_time:.1f} (queue size: {self.queue.qsize()})")
        except queue.Full:
            logger.error("Processing queue is full! Dropping trigger event to prevent memory pressure.")
            self.buffer_manager.unlock_chunks(locked)
            self.processing_errors += 1

    def _worker_loop(self):
        while not self._stop_event.is_set():
            try:
                job = self.queue.get(timeout=1.0)
            except queue.Empty:
                continue

            self.is_busy = True
            try:
                self._process_clip_job(job)
            except Exception as e:
                self.processing_errors += 1
                self.editing_status = "Error during edit"
                logger.error(f"Error processing clip job: {e}", exc_info=True)
            finally:
                # Unlock chunks so buffer manager can prune expired files
                self.buffer_manager.unlock_chunks(job.locked_chunks)
                self.queue.task_done()
                self.is_busy = False
                time.sleep(2)
                if self.queue.empty():
                    self.editing_status = "IDLE"
                    self.editing_progress_pct = 0.0

    def _process_clip_job(self, job: ClipJob):
        event = job.event
        now = time.time()
        
        # 1. Wait until reaction delay has passed so all post-event video is written (0% -> 20%)
        wait_needed = event.ready_time - now
        if wait_needed > 0:
            logger.info(f"Waiting {wait_needed:.1f}s for post-event reaction recording to complete...")
            start_wait = time.time()
            while time.time() < event.ready_time and not self._stop_event.is_set():
                elapsed = time.time() - start_wait
                rem = max(0.0, event.ready_time - time.time())
                self.editing_status = f"Buffering post-event ({int(rem)}s)"
                self.editing_progress_pct = min(20.0, (elapsed / wait_needed) * 20.0)
                time.sleep(0.5)

            # Re-lock any new chunk that was created during the reaction wait
            additional_chunks = self.buffer_manager.lock_chunks_for_range(
                event.clip_start_time,
                event.clip_end_time
            )
            job.locked_chunks.extend([c for c in additional_chunks if c not in job.locked_chunks])

        if not job.locked_chunks:
            logger.warning("No chunks available for clip reconstruction")
            self.processing_errors += 1
            self.editing_status = "No chunks available"
            return

        raw_clip_path = self.config.temp_dir / f"raw_assembly_{int(event.event_time)}.mp4"

        # 2. Reconstruct and slice 40s+ raw clip (20% -> 35%)
        target_dur = max(40.0, float(getattr(event, "clip_duration", 45.0)))
        self.editing_status = f"Stitching {len(job.locked_chunks)} chunks ({int(target_dur)}s)"
        self.editing_progress_pct = 25.0
        logger.info(f"Stitching {len(job.locked_chunks)} chunks for {target_dur:.1f}s clip (40s+)...")
        success = self.buffer_manager.reconstruct_clip(
            chunks=job.locked_chunks,
            target_start=event.clip_start_time,
            target_duration=target_dur,
            output_path=raw_clip_path
        )

        if not success or not raw_clip_path.exists():
            logger.error("Failed to assemble raw clip")
            self.processing_errors += 1
            self.editing_status = "Stitch failed"
            return

        # 3. Transcribe and extract subtitle cues (35% -> 50%)
        self.editing_status = "Transcribing (Faster-Whisper)"
        self.editing_progress_pct = 38.0
        logger.info("Transcribing clip audio with faster-whisper...")
        transcript, cues = self.transcriber.transcribe_audio(raw_clip_path)

        # 4. Retrieve relevant chat messages around trigger
        chat_context = self.chat_monitor.get_recent_messages(
            count=25,
            around_timestamp=event.event_time
        )

        # 5. Generate viral title & engaging hook banner (50% -> 60%)
        self.editing_status = "Generating viral hook & title"
        self.editing_progress_pct = 50.0
        title, hashtags = self.title_generator.generate_title_and_tags(
            transcript=transcript,
            chat_messages=chat_context,
            channel_name=self.config.channel
        )
        hook_text = self.title_generator.generate_hook(
            transcript=transcript,
            chat_messages=chat_context,
            channel_name=self.config.channel,
            title=title
        )
        self.current_hook_text = hook_text
        self.editing_progress_pct = 60.0

        # 6. Build sanitized filenames
        event_dt = time.localtime(event.event_time)
        time_prefix = time.strftime("%Y-%m-%d_%H-%M-%S", event_dt)
        short_title_slug = sanitize_filename(title)
        
        final_mp4_name = f"{time_prefix}_{short_title_slug}.mp4"
        final_txt_name = f"{time_prefix}_{short_title_slug}.txt"

        final_mp4_path = self.config.clips_dir / final_mp4_name
        final_txt_path = self.config.clips_dir / final_txt_name

        # 7. Render vertical video (1080x1920) with blurred background, hook & burned captions (60% -> 95%)
        self.editing_status = "Rendering 1080x1920: 0%"
        def _on_render_progress(render_pct: float):
            self.editing_progress_pct = min(95.0, 60.0 + (render_pct * 0.35))
            self.editing_status = f"Rendering Video: {int(render_pct)}%"

        logger.info(f"Rendering final vertical video with hook banner & captions...")
        render_success = self.video_processor.render_vertical_clip(
            raw_clip_path=raw_clip_path,
            output_clip_path=final_mp4_path,
            subtitle_cues=cues,
            transcriber=self.transcriber,
            hook_text=hook_text,
            on_progress=_on_render_progress
        )

        if not render_success:
            logger.error("Failed to render vertical video")
            self.processing_errors += 1
            self.editing_status = "Render failed"
            if raw_clip_path.exists():
                raw_clip_path.unlink()
            return

        # 8. Write matching TXT metadata file
        self.editing_status = "Saving metadata TXT"
        self.editing_progress_pct = 97.0
        try:
            with open(final_txt_path, "w", encoding="utf-8") as f:
                f.write(f"Title: {title}\n")
                f.write(f"Viral Hook: {hook_text}\n")
                f.write(f"Hashtags: {' '.join(hashtags)}\n")
                f.write(f"Event Timestamp: {time.strftime('%Y-%m-%d %H:%M:%S', event_dt)} (epoch: {event.event_time:.2f})\n")
                f.write(f"Transcript:\n{transcript}\n")
        except Exception as e:
            logger.error(f"Failed to write metadata TXT: {e}")

        # Cleanup raw clip
        if raw_clip_path.exists():
            try:
                raw_clip_path.unlink()
            except OSError:
                pass

        # 9. Required output print
        hh_mm_ss = time.strftime("%H:%M:%S", event_dt)
        print(f"[CLIP CREATED] {hh_mm_ss} | {title} | clips/{final_mp4_name}")
        logger.info(f"[CLIP CREATED] {hh_mm_ss} | {title} | clips/{final_mp4_name}")

        self.total_clips_created += 1
        self.last_created_clip_name = final_mp4_name
        self.editing_status = "Completed & saved!"
        self.editing_progress_pct = 100.0

