"""Live terminal dashboard for real-time system monitoring."""

import os
import sys
import time
import shutil
import logging
import threading
from typing import Optional

from live_clipper.recorder import RecordingEngine
from live_clipper.buffer_manager import BufferManager
from live_clipper.chat_monitor import TwitchChatMonitor
from live_clipper.audio_analyzer import AudioAnalyzer
from live_clipper.trigger_engine import TriggerEngine
from live_clipper.worker import ProcessingWorker

logger = logging.getLogger("live_clipper.dashboard")


class LiveDashboard:
    """
    Renders an in-place live terminal dashboard refreshing every second
    without flooding log streams.
    """
    def __init__(
        self,
        recorder: RecordingEngine,
        buffer_manager: BufferManager,
        chat_monitor: TwitchChatMonitor,
        audio_analyzer: AudioAnalyzer,
        trigger_engine: TriggerEngine,
        worker: ProcessingWorker,
        refresh_interval: float = 1.0
    ):
        self.recorder = recorder
        self.buffer_manager = buffer_manager
        self.chat_monitor = chat_monitor
        self.audio_analyzer = audio_analyzer
        self.trigger_engine = trigger_engine
        self.worker = worker
        self.refresh_interval = refresh_interval
        
        self.start_time = time.time()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self):
        """Start dashboard refresh loop in background thread."""
        if self._thread and self._thread.is_alive():
            return
        self.start_time = time.time()
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop, name="DashboardThread", daemon=True)
        self._thread.start()

    def stop(self):
        """Stop dashboard thread."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=2)

    def _get_disk_free_gb(self) -> float:
        try:
            stat = shutil.disk_usage(self.recorder.config.base_dir)
            return stat.free / (1024 ** 3)
        except Exception:
            return 0.0

    def get_dashboard_text(self) -> str:
        """Construct the formatted dashboard string with exact borders."""
        cfg = self.recorder.config
        now_str = time.strftime("%Y-%m-%d %H:%M:%S")
        runtime_sec = int(time.time() - self.start_time)
        runtime_str = f"{runtime_sec // 60}m {runtime_sec % 60}s"
        if cfg.max_duration_sec:
            runtime_str += f" (max {cfg.max_duration_sec // 60}m)"

        # Stream metrics
        stream_status = self.recorder.status.upper()
        stream_quality = self.recorder.selected_quality
        chunks_count = self.buffer_manager.get_active_chunk_count()
        buffer_dur = self.buffer_manager.get_buffer_duration()

        # Chat metrics
        irc_status = "CONNECTED" if self.chat_monitor.is_connected else "DISCONNECTED"
        chat_rate = self.chat_monitor.get_current_chat_rate()
        chat_avg = self.chat_monitor.get_rolling_chat_average()
        warmup_pct = self.chat_monitor.warmup_progress_pct
        warmup_status = "READY (100%)" if self.chat_monitor.is_warmed_up else f"{warmup_pct:.0f}% ({int(self.chat_monitor.warmup_elapsed_sec)}s/{cfg.warmup_duration_sec}s)"

        chat_target = max(chat_avg * cfg.chat_spike_multiplier, float(cfg.min_chat_rate))
        chat_target_str = f">= {chat_target:.1f} ({cfg.chat_spike_multiplier:.1f}x)"

        # Audio metrics
        loudness = self.audio_analyzer.current_loudness
        audio_baseline = self.audio_analyzer.current_baseline
        loudness_str = f"{loudness:.1f} LUFS" if loudness > -90.0 else "SILENCE"
        audio_base_str = f"{audio_baseline:.1f} LUFS"
        audio_target = audio_baseline + cfg.audio_spike_db
        audio_target_str = f">= {audio_target:.1f} (+{cfg.audio_spike_db:.0f}dB)"

        # Trigger metrics
        trig_status = self.trigger_engine.trigger_status
        cooldown = self.trigger_engine.cooldown_remaining
        cooldown_str = f"{cooldown:.1f}s" if cooldown > 0 else "READY"
        mode_str = getattr(cfg, "trigger_mode", "dual").upper()

        # Worker & Disk metrics
        clips_created = self.worker.total_clips_created
        if cfg.max_clips:
            clips_str = f"{clips_created} / {cfg.max_clips} clips"
        else:
            clips_str = f"{clips_created} created"
        errors = self.worker.processing_errors
        queue_size = self.worker.queue.qsize()
        disk_free = self._get_disk_free_gb()
        last_clip = self.worker.last_created_clip_name or "None yet"

        # Box dimensions: exact 66 inner content width + 2 border padding = 70 chars wide
        INNER = 66
        BORDER_LEN = INNER + 2 # 68

        def row(content: str) -> str:
            line_txt = content[:INNER]
            return f"║ {line_txt:<{INNER}} ║\n"

        def two_col(left: str, right: str) -> str:
            l_str = left[:32].ljust(32)
            r_str = right[:32].ljust(32)
            return row(f"{l_str}  {r_str}")

        top_border = f"╔{'═' * BORDER_LEN}╗\n"
        div_border = f"╠{'═' * BORDER_LEN}╣\n"
        bot_border = f"╚{'═' * BORDER_LEN}╝\n"

        lines = [
            "\033[H\033[J", # ANSI home & clear screen
            top_border,
            row(f"TWITCH LIVE CLIPPER  •  CHANNEL: #{cfg.channel.upper()}".center(INNER)),
            row(f"Current Time: {now_str}  •  Trigger Mode: {mode_str}".center(INNER)),
            div_border,
            two_col(f"STREAM STATUS:   {stream_status}", f"STREAM QUALITY:  {stream_quality}"),
            two_col(f"CHUNK BUFFER:    {chunks_count} chunks ({buffer_dur:.0f}s)", f"BUFFER RETENTION:10 min ({cfg.buffer_retention_sec}s)"),
            div_border,
            two_col(f"IRC CHAT:        {irc_status}", f"WARMUP STATUS:   {warmup_status}"),
            two_col(f"CHAT RATE (5s):  {chat_rate:.0f} msgs/5s", f"CHAT BASELINE:   {chat_avg:.1f} msgs/5s"),
            two_col(f"CHAT SPIKE GOAL: {chat_target_str}", f"MIN CHAT RATE:   {cfg.min_chat_rate} msgs/5s"),
            two_col(f"AUDIO LOUDEST 1s:{loudness_str}", f"AUDIO BASELINE:  {audio_base_str}"),
            two_col(f"AUDIO SPIKE GOAL:{audio_target_str}", f"COOLDOWN:        {cooldown_str}"),
            div_border,
            two_col(f"TRIGGER STATUS:  {trig_status}", f"CLIPS CREATED:   {clips_str}"),
            two_col(f"QUEUED JOBS:     {queue_size} jobs", f"SESSION RUNTIME: {runtime_str}"),
            two_col(f"PROCESSING ERR:  {errors}", f"DISK FREE:       {disk_free:.1f} GB"),
            div_border,
            row(f"LATEST CLIP: {last_clip[:52]}"),
            bot_border
        ]
        return "".join(lines)

    def _loop(self):
        while not self._stop_event.is_set():
            try:
                # Render only if stdout is a TTY or in live run
                if sys.stdout.isatty():
                    sys.stdout.write(self.get_dashboard_text())
                    sys.stdout.flush()
            except Exception:
                pass
            time.sleep(self.refresh_interval)

