"""Main orchestrator and CLI application for live-clipper."""

import os
import sys
import time
import signal
import logging
import argparse
from pathlib import Path

# Ensure we are running inside the project's Python 3.11 virtual environment
_pkg_root = Path(__file__).resolve().parent.parent
_venv_python = _pkg_root / ".venv" / "bin" / "python"
if _venv_python.exists() and Path(sys.executable).resolve() != _venv_python.resolve():
    os.execv(str(_venv_python), [str(_venv_python)] + sys.argv)

if str(_pkg_root) not in sys.path:
    sys.path.insert(0, str(_pkg_root))

from live_clipper.config import ClipperConfig
from live_clipper.recorder import RecordingEngine
from live_clipper.buffer_manager import BufferManager, Chunk
from live_clipper.chat_monitor import TwitchChatMonitor
from live_clipper.audio_analyzer import AudioAnalyzer
from live_clipper.trigger_engine import TriggerEngine, TriggerEvent
from live_clipper.transcriber import Transcriber
from live_clipper.video_processor import VideoProcessor
from live_clipper.title_generator import TitleGenerator
from live_clipper.worker import ProcessingWorker
from live_clipper.dashboard import LiveDashboard

class AutoFlushFileHandler(logging.FileHandler):
    """FileHandler that flushes immediately on every write for realtime IDE log viewing."""
    def emit(self, record):
        super().emit(record)
        self.flush()

# Configure logging with immediate flush to both workspace root and package logs
_log_paths = [
    _pkg_root / "live-clipper.log",
    _pkg_root.parent / "live-clipper.log"
]
_handlers = []
_seen_paths = set()
for lp in _log_paths:
    try:
        resolved = lp.resolve()
        if resolved not in _seen_paths:
            _seen_paths.add(resolved)
            _handlers.append(AutoFlushFileHandler(str(lp), mode="a", encoding="utf-8"))
    except Exception:
        pass

if not _handlers:
    _handlers.append(AutoFlushFileHandler("live-clipper.log", mode="a", encoding="utf-8"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=_handlers
)
logger = logging.getLogger("live_clipper.app")


class LiveClipperApp:
    def __init__(self, config: ClipperConfig):
        self.config = config
        
        # 1. Buffer storage
        self.buffer_manager = BufferManager(
            temp_dir=config.temp_dir,
            retention_sec=config.buffer_retention_sec
        )

        # 2. Chat monitor
        self.chat_monitor = TwitchChatMonitor(config)

        # 3. Audio analyzer
        self.audio_analyzer = AudioAnalyzer(config)

        # 4. Trigger engine
        self.trigger_engine = TriggerEngine(
            config=config,
            chat_monitor=self.chat_monitor,
            audio_analyzer=self.audio_analyzer,
            buffer_manager=self.buffer_manager
        )

        # 5. ML & Video processing pipelines
        self.transcriber = Transcriber(config)
        self.video_processor = VideoProcessor(config)
        self.title_generator = TitleGenerator(config)

        # 6. Decoupled worker
        self.worker = ProcessingWorker(
            config=config,
            buffer_manager=self.buffer_manager,
            chat_monitor=self.chat_monitor,
            transcriber=self.transcriber,
            video_processor=self.video_processor,
            title_generator=self.title_generator
        )

        # 7. Recorder
        self.recorder = RecordingEngine(
            config=config,
            buffer_manager=self.buffer_manager,
            on_chunk_completed=self._on_chunk_completed
        )

        # 8. Dashboard
        self.dashboard = LiveDashboard(
            recorder=self.recorder,
            buffer_manager=self.buffer_manager,
            chat_monitor=self.chat_monitor,
            audio_analyzer=self.audio_analyzer,
            trigger_engine=self.trigger_engine,
            worker=self.worker
        )

        self._running = False
        self.start_time: float = 0.0

    def _on_chunk_completed(self, chunk: Chunk):
        """Callback invoked whenever a 10-second chunk is completed."""
        try:
            # 1. Measure audio loudness of chunk
            now = chunk.start_time
            loudness = self.audio_analyzer.analyze_chunk(chunk.path, now)
            baseline = self.audio_analyzer.current_baseline
            
            # 2. Check for audio spike
            self.trigger_engine.record_audio_sample(now, loudness, baseline)

            # 3. Evaluate dual trigger
            event = self.trigger_engine.evaluate(now)
            if event:
                self.worker.submit_job(event)

        except Exception as e:
            logger.error(f"Error handling completed chunk {chunk.path.name}: {e}", exc_info=True)

    def check_stream_preflight(self) -> bool:
        """Check if stream is live before starting services."""
        plat = self.config.platform.upper()
        target = self.config.stream_url or f"#{self.config.channel}"
        print(f"[*] Checking {plat} stream status for {target}...")
        is_online, quality, _ = self.recorder.check_stream_online()
        if not is_online:
            print(f"\n╔{'═'*66}╗")
            print(f"║ [STATUS: OFFLINE] {plat} Stream: {target[:35]:<35} ║")
            print(f"║ Stream is currently NOT live.                                    ║")
            print(f"║ Exiting with status: OFFLINE.                                    ║")
            print(f"║ (Pass --retry-offline to stay in standby until stream starts)     ║")
            print(f"╚{'═'*66}╝\n")
            return False
        print(f"[+] {plat} Stream {target} is LIVE! Selected quality: {quality}")
        print(f"[+] Video Layout: {self.config.video_layout.upper()} (Top Streamer, Bottom Game)")
        return True

    def start(self):
        """Start all services."""
        self._running = True
        self.start_time = time.time()
        logger.info(f"Starting Live Clipper for {self.config.platform.upper()}: {self.config.stream_url}")
        
        if self.config.platform == "twitch":
            self.chat_monitor.start()
        self.worker.start()
        self.recorder.start()
        self.dashboard.start()

    def stop(self):
        """Stop all services gracefully."""
        self._running = False
        logger.info("Stopping all services...")
        self.dashboard.stop()
        self.recorder.stop()
        if self.config.platform == "twitch":
            self.chat_monitor.stop()
        self.worker.stop()
        logger.info("Live Clipper stopped cleanly")

    def run_forever(self):
        """Run until SIGINT or stream ends or limits reached."""
        # Pre-flight check if offline auto-exit is enabled
        if not self.config.retry_offline:
            if not self.check_stream_preflight():
                return

        self.start()
        
        def _handle_signal(sig, frame):
            print("\nShutting down gracefully...")
            self.stop()
            sys.exit(0)

        signal.signal(signal.SIGINT, _handle_signal)
        signal.signal(signal.SIGTERM, _handle_signal)

        try:
            while self._running:
                # If stream drops mid-recording and retry_offline is False, stop
                if not self.config.retry_offline and self.recorder.status == "offline":
                    print(f"\n[STATUS: OFFLINE] Stream on #{self.config.channel} has ended. Shutting down clipper.")
                    self.stop()
                    break

                # Check max clips limit
                if self.config.max_clips and self.worker.total_clips_created >= self.config.max_clips:
                    print(f"\n[LIMIT REACHED] Reached {self.worker.total_clips_created} clip(s) (limit: {self.config.max_clips}). Shutting down clipper.")
                    self.stop()
                    break

                # Check max duration limit
                if self.config.max_duration_sec and (time.time() - self.start_time) >= self.config.max_duration_sec:
                    elapsed = int(time.time() - self.start_time)
                    print(f"\n[LIMIT REACHED] Reached session duration limit of {self.config.max_duration_sec}s (ran for {elapsed}s). Shutting down clipper.")
                    self.stop()
                    break

                time.sleep(1)
        except KeyboardInterrupt:
            self.stop()


def main():
    parser = argparse.ArgumentParser(description="Live Clipper for Twitch and YouTube")
    parser.add_argument("--url", type=str, default="", help="Direct Twitch or YouTube stream link (e.g. https://youtube.com/live/xyz or https://twitch.tv/xyz)")
    parser.add_argument("--channel", type=str, default="ishowspeed", help="Twitch channel or streamer name (default: ishowspeed)")
    parser.add_argument("--layout", type=str, choices=["split", "fit_blur"], default="split", help="Video layout: split (top streamer, bottom game) or fit_blur (default: split)")
    parser.add_argument("--facecam", type=str, choices=["auto", "top-right", "bottom-right", "top-left", "bottom-left", "center"], default="auto", help="Streamer facecam position for split layout (default: auto)")
    parser.add_argument("--divider", type=str, choices=["yellow", "white", "cyan", "none"], default="yellow", help="Split screen divider line color (default: yellow)")
    parser.add_argument("--quality", type=str, default="720p,720p60,best", help="Preferred stream quality")
    parser.add_argument("--warmup", type=int, default=300, help="Warmup duration in seconds before triggers")
    parser.add_argument("--cooldown", type=int, default=120, help="Cooldown in seconds between clips")
    parser.add_argument("--retry-offline", action="store_true", help="Keep running in standby if channel is offline")
    parser.add_argument("--max-clips", type=int, default=None, help="Auto-stop after generating N clips (e.g. --max-clips 3)")
    parser.add_argument("--max-duration", type=int, default=None, help="Auto-stop after running for N seconds (e.g. --max-duration 3600)")
    parser.add_argument("--trigger-mode", type=str, choices=["dual", "any", "chat", "audio"], default="dual", help="Trigger criteria: dual (chat & audio), any (either), chat, audio")
    parser.add_argument("--chat-spike", "--chat-multiplier", dest="chat_spike", type=float, default=3.0, help="Chat multiplier threshold (e.g. 2.0 or 3.0)")
    parser.add_argument("--audio-spike", "--audio-db", dest="audio_spike", type=float, default=6.0, help="Audio dB increase threshold (e.g. 4.0 or 6.0)")
    parser.add_argument("--min-chat-rate", type=int, default=3, help="Minimum msgs/5s required for a chat spike (default: 3)")
    args = parser.parse_args()

    config = ClipperConfig(
        channel=args.channel,
        stream_url=args.url,
        video_layout=args.layout,
        facecam_position=args.facecam,
        divider_color=args.divider,
        preferred_quality=args.quality,
        warmup_duration_sec=args.warmup,
        cooldown_sec=args.cooldown,
        retry_offline=args.retry_offline,
        max_clips=args.max_clips,
        max_duration_sec=args.max_duration,
        trigger_mode=args.trigger_mode,
        chat_spike_multiplier=args.chat_spike,
        audio_spike_db=args.audio_spike,
        min_chat_rate=args.min_chat_rate
    )

    app = LiveClipperApp(config)
    app.run_forever()


if __name__ == "__main__":
    main()
