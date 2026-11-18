"""Trigger Engine for correlating chat and audio spikes and enforcing warmup & cooldown."""

import time
import logging
import threading
from typing import Optional, List, Dict, Tuple
from dataclasses import dataclass

from live_clipper.config import ClipperConfig
from live_clipper.chat_monitor import TwitchChatMonitor
from live_clipper.audio_analyzer import AudioAnalyzer

logger = logging.getLogger("live_clipper.trigger_engine")


@dataclass
class TriggerEvent:
    event_time: float
    clip_start_time: float
    clip_end_time: float
    clip_duration: float
    ready_time: float # When enough post-event video is recorded to extract the clip
    
    chat_rate: float
    chat_baseline: float
    audio_loudness: float
    audio_baseline: float


class TriggerEngine:
    """
    Evaluates trigger criteria:
      1. Chat activity >= 3x rolling chat average
      2. Audio loudness >= rolling average + 6 dB
      3. Both events occur within 15 seconds of each other
      4. Requires 5 complete minutes of chat data before triggers can fire
      5. Enforces a 120-second cooldown after a trigger
      6. Slices 30 seconds before and 15 seconds after event (45s clip)
    """
    def __init__(
        self,
        config: ClipperConfig,
        chat_monitor: TwitchChatMonitor,
        audio_analyzer: AudioAnalyzer
    ):
        self.config = config
        self.chat_monitor = chat_monitor
        self.audio_analyzer = audio_analyzer
        
        self._lock = threading.RLock()
        self.last_trigger_time: float = 0.0
        self.total_triggers_fired: int = 0
        
        # Lists of (timestamp, value, baseline)
        self.recent_chat_spikes: List[Tuple[float, float, float]] = []
        self.recent_audio_spikes: List[Tuple[float, float, float]] = []

    @property
    def cooldown_remaining(self) -> float:
        """Seconds remaining in cooldown, or 0.0 if ready."""
        with self._lock:
            elapsed = time.time() - self.last_trigger_time
            if elapsed < self.config.cooldown_sec:
                return round(self.config.cooldown_sec - elapsed, 1)
            return 0.0

    @property
    def is_in_cooldown(self) -> bool:
        return self.cooldown_remaining > 0.0

    @property
    def trigger_status(self) -> str:
        """Human-readable status for the live dashboard."""
        if getattr(self.config, "platform", "twitch") == "twitch" and not self.chat_monitor.is_warmed_up:
            return f"Warmup ({self.chat_monitor.warmup_progress_pct:.0f}%)"
        if self.is_in_cooldown:
            return f"Cooldown ({self.cooldown_remaining:.0f}s)"
        return "Monitoring"

    def record_audio_sample(self, timestamp: float, loudness: float, baseline: float):
        """Record an audio measurement and check if it qualifies as an audio spike."""
        with self._lock:
            # Audio spike: loudness >= baseline + 6 dB
            if loudness >= (baseline + self.config.audio_spike_db):
                logger.info(f"AUDIO SPIKE detected! Loudness: {loudness:.1f} LUFS (baseline: {baseline:.1f} LUFS, spike: +{loudness - baseline:.1f} dB)")
                self.recent_audio_spikes.append((timestamp, loudness, baseline))
                self._prune_spikes(timestamp)

    def record_chat_sample(self, timestamp: float, chat_rate: float, baseline: float):
        """Record a chat measurement and check if it qualifies as a chat spike."""
        with self._lock:
            # Chat spike: chat_rate >= multiplier * baseline and >= min_chat_rate
            if chat_rate >= (baseline * self.config.chat_spike_multiplier) and chat_rate >= self.config.min_chat_rate:
                logger.info(f"CHAT SPIKE detected! Rate: {chat_rate:.0f} msgs/5s (baseline: {baseline:.1f}, multiplier: {chat_rate / baseline:.1f}x)")
                self.recent_chat_spikes.append((timestamp, chat_rate, baseline))
                self._prune_spikes(timestamp)

    def _prune_spikes(self, current_time: float):
        """Remove spikes older than the correlation window (15s)."""
        cutoff = current_time - (self.config.correlation_window_sec * 2)
        self.recent_audio_spikes = [s for s in self.recent_audio_spikes if s[0] >= cutoff]
        self.recent_chat_spikes = [s for s in self.recent_chat_spikes if s[0] >= cutoff]

    def evaluate(self, current_time: Optional[float] = None) -> Optional[TriggerEvent]:
        """
        Evaluate if both chat and audio spike conditions have been met within 15 seconds.
        Returns a TriggerEvent if fired, else None.
        """
        now = current_time or time.time()

        with self._lock:
            # 1. Warmup guard: Enforce warmup for Twitch chat data
            if getattr(self.config, "platform", "twitch") == "twitch" and not self.chat_monitor.is_warmed_up:
                return None

            # 2. Cooldown guard
            if self.is_in_cooldown:
                return None

            # 3. Check current chat rate as well (in case a spike is happening right now)
            current_chat_rate = self.chat_monitor.get_current_chat_rate(now)
            chat_baseline = self.chat_monitor.get_rolling_chat_average(exclude_current_spike=True, timestamp=now)
            if current_chat_rate >= (chat_baseline * self.config.chat_spike_multiplier) and current_chat_rate >= self.config.min_chat_rate:
                self.record_chat_sample(now, current_chat_rate, chat_baseline)

            self._prune_spikes(now)

            # 4. Correlation check: Look for matching audio and chat spikes within correlation window
            matched_audio = None
            matched_chat = None

            for a_time, a_lufs, a_base in reversed(self.recent_audio_spikes):
                for c_time, c_rate, c_base in reversed(self.recent_chat_spikes):
                    time_diff = abs(a_time - c_time)
                    if time_diff <= self.config.correlation_window_sec:
                        matched_audio = (a_time, a_lufs, a_base)
                        matched_chat = (c_time, c_rate, c_base)
                        break
                if matched_audio:
                    break

            mode = getattr(self.config, "trigger_mode", "dual")
            if mode == "dual":
                if not matched_audio or not matched_chat:
                    return None
                event_time = max(matched_audio[0], matched_chat[0])
                c_rate = matched_chat[1]
                c_base = matched_chat[2]
                a_lufs = matched_audio[1]
                a_base = matched_audio[2]
            elif mode == "chat":
                if not self.recent_chat_spikes:
                    return None
                c_time, c_rate, c_base = self.recent_chat_spikes[-1]
                event_time = c_time
                a_lufs = self.audio_analyzer.current_loudness
                a_base = self.audio_analyzer.current_baseline
            elif mode == "audio":
                if not self.recent_audio_spikes:
                    return None
                a_time, a_lufs, a_base = self.recent_audio_spikes[-1]
                event_time = a_time
                c_rate = self.chat_monitor.get_current_chat_rate(now)
                c_base = self.chat_monitor.get_rolling_chat_average(exclude_current_spike=True, timestamp=now)
            elif mode == "any":
                if matched_audio and matched_chat:
                    event_time = max(matched_audio[0], matched_chat[0])
                    c_rate = matched_chat[1]
                    c_base = matched_chat[2]
                    a_lufs = matched_audio[1]
                    a_base = matched_audio[2]
                elif self.recent_chat_spikes:
                    c_time, c_rate, c_base = self.recent_chat_spikes[-1]
                    event_time = c_time
                    a_lufs = self.audio_analyzer.current_loudness
                    a_base = self.audio_analyzer.current_baseline
                elif self.recent_audio_spikes:
                    a_time, a_lufs, a_base = self.recent_audio_spikes[-1]
                    event_time = a_time
                    c_rate = self.chat_monitor.get_current_chat_rate(now)
                    c_base = self.chat_monitor.get_rolling_chat_average(exclude_current_spike=True, timestamp=now)
                else:
                    return None
            else:
                if not matched_audio or not matched_chat:
                    return None
                event_time = max(matched_audio[0], matched_chat[0])
                c_rate = matched_chat[1]
                c_base = matched_chat[2]
                a_lufs = matched_audio[1]
                a_base = matched_audio[2]

            # Valid trigger found!
            # Slicing: 30 seconds before event, 15 seconds after event = 45s clip
            clip_start = event_time - self.config.clip_pre_sec
            clip_end = event_time + self.config.clip_post_sec
            clip_duration = self.config.clip_duration_sec
            ready_time = event_time + self.config.reaction_delay_sec

            event = TriggerEvent(
                event_time=event_time,
                clip_start_time=clip_start,
                clip_end_time=clip_end,
                clip_duration=clip_duration,
                ready_time=ready_time,
                chat_rate=c_rate,
                chat_baseline=c_base,
                audio_loudness=a_lufs,
                audio_baseline=a_base
            )

            # Update cooldown and statistics
            self.last_trigger_time = now
            self.total_triggers_fired += 1
            
            # Clear used spikes
            self.recent_audio_spikes.clear()
            self.recent_chat_spikes.clear()

            logger.info(
                f"🔥 DUAL TRIGGER FIRED at {time.strftime('%H:%M:%S', time.localtime(event_time))}! "
                f"Chat: {event.chat_rate:.0f} (base: {event.chat_baseline:.1f}), "
                f"Audio: {event.audio_loudness:.1f} LUFS (base: {event.audio_baseline:.1f} LUFS). "
                f"Clip: [{clip_start:.1f} -> {clip_end:.1f}]"
            )
            return event

