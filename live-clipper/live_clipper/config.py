"""Configuration parameters for live-clipper."""

import os
from pathlib import Path
from typing import Optional
from dataclasses import dataclass, field

BASE_DIR = Path(__file__).resolve().parent.parent

@dataclass
class ClipperConfig:
    channel: str = "ishowspeed"
    stream_url: str = "https://www.twitch.tv/ishowspeed"
    preferred_quality: str = "720p,720p60,best"
    
    # Buffer and Chunk settings
    chunk_duration_sec: int = 10
    buffer_retention_sec: int = 600  # 10 minutes rolling buffer
    max_chunks_retained: int = 65    # 600s / 10s = 60 + safety margin
    
    # Twitch IRC Chat settings
    irc_host: str = "irc.chat.twitch.tv"
    irc_port: int = 6667
    chat_bucket_sec: int = 5         # 5-second message bucket counter
    chat_rolling_window_sec: int = 300 # 5-minute rolling average
    warmup_duration_sec: int = 300     # 5 complete minutes of data before triggers
    
    # Audio settings
    audio_rolling_window_sec: int = 300 # 5-minute rolling audio baseline
    
    # Trigger thresholds
    chat_spike_multiplier: float = 3.0 # Chat activity >= 3x rolling average
    audio_spike_db: float = 6.0        # Audio loudness >= rolling average + 6 dB
    min_chat_rate: int = 3             # Minimum msgs/5s for a chat spike
    trigger_mode: str = "dual"         # "dual" (chat & audio), "any" (either), "chat", "audio"
    correlation_window_sec: float = 15.0 # Both events within 15 seconds
    reaction_delay_sec: float = 15.0     # Wait 15s for reaction after trigger
    clip_pre_sec: float = 30.0          # 30s before event
    clip_post_sec: float = 15.0         # 15s after event
    clip_duration_sec: float = 45.0     # 45-second clip total
    cooldown_sec: float = 120.0         # 120s cooldown

    # Dynamic limits
    max_clips: Optional[int] = None     # Maximum clips before auto-stop
    max_duration_sec: Optional[int] = None # Maximum runtime in seconds before auto-stop
    
    # Paths
    base_dir: Path = BASE_DIR
    clips_dir: Path = field(default_factory=lambda: BASE_DIR / "clips")
    temp_dir: Path = field(default_factory=lambda: BASE_DIR / "temp")
    
    # Whisper settings
    whisper_model: str = "base"
    whisper_device: str = "cpu"
    whisper_compute_type: str = "int8"
    
    # Gemini CLI
    gemini_cli_path: str = "gemini"
    
    # Offline behavior: if False, exits immediately when channel is offline
    retry_offline: bool = False
    
    def __post_init__(self):
        self.stream_url = f"https://www.twitch.tv/{self.channel.lower()}"
        self.clips_dir.mkdir(parents=True, exist_ok=True)
        self.temp_dir.mkdir(parents=True, exist_ok=True)

