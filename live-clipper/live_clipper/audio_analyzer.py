"""FFmpeg EBU R128 Audio Analyzer for measuring loudness and maintaining a 5-minute rolling baseline."""

import re
import math
import logging
import threading
import subprocess
from pathlib import Path
from typing import List, Tuple, Optional, Deque
from collections import deque

from live_clipper.config import ClipperConfig

logger = logging.getLogger("live_clipper.audio_analyzer")

# Regex pattern to match ebur128 output: t: 0.500 ... M: -18.2 ...
EBUR128_PATTERN = re.compile(r"t:\s*([\d\.]+)\s+.*?M:\s*([-\d\.]+)")


class AudioAnalyzer:
    """
    Measures the loudest 1-second interval in 10-second chunks using FFmpeg ebur128.
    Maintains a 5-minute rolling audio baseline and handles silence/missing audio safely.
    """
    DEFAULT_SILENCE_LUFS = -70.0
    DEFAULT_BASELINE_LUFS = -24.0

    def __init__(self, config: ClipperConfig):
        self.config = config
        self.rolling_window_sec = config.audio_rolling_window_sec # 300s (5 min)
        
        self._lock = threading.RLock()
        # History of (chunk_end_time, loudness_lufs)
        self.history: Deque[Tuple[float, float]] = deque(maxlen=60)
        self.current_loudness: float = self.DEFAULT_SILENCE_LUFS
        self.current_baseline: float = self.DEFAULT_BASELINE_LUFS

    def analyze_chunk(self, chunk_path: Path, timestamp: float) -> float:
        """
        Analyze a 10-second chunk file with FFmpeg ebur128.
        Returns the loudest 1-second interval in LUFS.
        """
        chunk_path = Path(chunk_path)
        if not chunk_path.exists() or chunk_path.stat().st_size == 0:
            logger.warning(f"Chunk file missing or empty: {chunk_path}")
            return self.DEFAULT_SILENCE_LUFS

        cmd = [
            "ffmpeg", "-nostats", "-v", "info",
            "-i", str(chunk_path),
            "-af", "ebur128",
            "-f", "null", "-"
        ]

        try:
            res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20)
            stderr_text = res.stderr.decode(errors="ignore")
            
            loudness = self._extract_loudest_one_second(stderr_text)
            
            with self._lock:
                self.current_loudness = loudness
                # Baseline strictly computed BEFORE adding current chunk
                self.current_baseline = self.get_rolling_baseline(exclude_current=False)
                # Now record this chunk into history
                self.history.append((timestamp, loudness))
                
            logger.debug(f"Chunk {chunk_path.name} loudness: {loudness:.1f} LUFS (baseline: {self.current_baseline:.1f} LUFS)")
            return loudness

        except subprocess.TimeoutExpired:
            logger.error(f"FFmpeg ebur128 timed out on {chunk_path.name}")
            return self.DEFAULT_SILENCE_LUFS
        except Exception as e:
            logger.error(f"Error analyzing audio for {chunk_path.name}: {e}")
            return self.DEFAULT_SILENCE_LUFS

    def _extract_loudest_one_second(self, stderr_text: str) -> float:
        """
        Extract Momentary loudness (M) values and compute the maximum 1-second sliding window average.
        FFmpeg ebur128 outputs samples every ~100ms. 1 second = ~10 samples.
        """
        m_samples: List[float] = []
        for line in stderr_text.splitlines():
            match = EBUR128_PATTERN.search(line)
            if match:
                try:
                    m_val = float(match.group(2))
                    # Ignore extreme uninitialized filter values like -120.7
                    if m_val > -100.0:
                        m_samples.append(m_val)
                except ValueError:
                    pass

        if not m_samples:
            # Check if integrated loudness is present in summary
            for line in stderr_text.splitlines():
                if "I:" in line and "LUFS" in line:
                    parts = line.split("I:", 1)[1].split("LUFS", 1)[0].strip()
                    try:
                        return float(parts)
                    except ValueError:
                        pass
            return self.DEFAULT_SILENCE_LUFS

        if len(m_samples) <= 10:
            # Short sample or few frames, take maximum
            return max(m_samples)

        # Sliding window of 10 samples (approx 1.0 second)
        # Convert dB/LUFS to linear power for accurate energy averaging: p = 10^(m/10)
        window_size = 10
        max_power = 0.0

        linear_powers = [10.0 ** (m / 10.0) for m in m_samples]
        current_sum = sum(linear_powers[:window_size])
        max_power = current_sum / window_size

        for i in range(window_size, len(linear_powers)):
            current_sum += linear_powers[i] - linear_powers[i - window_size]
            avg_power = current_sum / window_size
            if avg_power > max_power:
                max_power = avg_power

        if max_power <= 0:
            return self.DEFAULT_SILENCE_LUFS

        loudest_lufs = 10.0 * math.log10(max_power)
        return round(loudest_lufs, 2)

    def get_rolling_baseline(self, exclude_current: bool = True) -> float:
        """
        Calculate the 5-minute rolling audio baseline in LUFS.
        Excludes the current spike chunk when requested.
        """
        with self._lock:
            samples = list(self.history)
            if exclude_current and samples:
                samples = samples[:-1]

            if not samples:
                return self.DEFAULT_BASELINE_LUFS

            # Filter valid samples (non-silence) in the rolling window
            valid_samples = [lufs for (_, lufs) in samples if lufs > -60.0]
            if not valid_samples:
                return self.DEFAULT_BASELINE_LUFS

            # Mean in linear energy space for acoustic accuracy
            total_power = sum(10.0 ** (l / 10.0) for l in valid_samples)
            mean_power = total_power / len(valid_samples)
            if mean_power <= 0:
                return self.DEFAULT_BASELINE_LUFS

            baseline = 10.0 * math.log10(mean_power)
            return round(baseline, 2)

