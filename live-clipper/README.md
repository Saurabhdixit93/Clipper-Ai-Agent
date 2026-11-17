# Twitch Live Clipper (`live-clipper`)

A high-performance, autonomous Twitch live clipping system written in Python. It continuously records live streams into a 10-minute rolling buffer, monitors anonymous Twitch IRC chat rates, analyzes audio loudness via FFmpeg EBU R128, detects dual excitement spikes, and automatically renders 1080x1920 vertical shorts with blurred backgrounds, burned animated captions (`faster-whisper`), and AI titles with metadata.

---

## Architecture Overview

```
                          ┌────────────────────────┐
                          │  Twitch Stream (Live)  │
                          └───────────┬────────────┘
                                      │
              ┌───────────────────────┴───────────────────────┐
              ▼                                               ▼
   ┌──────────────────────┐                       ┌──────────────────────┐
   │ Streamlink + FFmpeg  │                       │   Twitch IRC Client  │
   │  Continuous 10s MP4  │                       │ Anonymous (#channel) │
   │    Rolling Buffer    │                       │  5s Bucket Counters  │
   └──────────┬───────────┘                       └───────────┬──────────┘
              │                                               │
              ▼                                               ▼
   ┌──────────────────────┐                       ┌──────────────────────┐
   │ FFmpeg EBU R128      │                       │ 5-Min Rolling Avg    │
   │ Loudest 1s Interval  │                       │ 3x Spike Detection   │
   │ Rolling Audio Avg    │                       │ 5-Min Warmup Guard   │
   └──────────┬───────────┘                       └───────────┬──────────┘
              │                                               │
              └───────────────────────┬───────────────────────┘
                                      │
                                      ▼
                        ┌───────────────────────────┐
                        │       Trigger Engine      │
                        │ Chat >= 3x & Audio >= +6dB│
                        │    Within 15s Window      │
                        │    120s Cooldown Guard    │
                        └─────────────┬─────────────┘
                                      │ (Trigger fired)
                                      ▼
                        ┌───────────────────────────┐
                        │ Decoupled Background Queue│
                        │ 1. Stitch [-30s, +15s]    │
                        │ 2. faster-whisper (CPU)   │
                        │ 3. Gemini / Local Title   │
                        │ 4. 1080x1920 Blurred Vert │
                        │ 5. Burned Bold Captions   │
                        │ 6. MP4 + Metadata .txt    │
                        └───────────────────────────┘
```

---

## Core Features

1. **Decoupled Recording Engine**:
   - Uses Streamlink to capture live video (prefers `720p,720p60` with automatic best-quality fallback).
   - Generates timestamped 10-second segments using FFmpeg copy muxing (zero CPU transcoding overhead).
   - Manages a 10-minute rolling buffer (automatically purges expired chunks while protecting actively processing chunks via reference counting).
   - Automatically reconnects with exponential backoff on stream drops.

2. **Anonymous Twitch IRC Monitor**:
   - Connects to `irc.chat.twitch.tv:6667` anonymously (`justinfan` + random digits, no OAuth required).
   - Handles `PING`/`PONG` heartbeats and auto-reconnects.
   - Buckets messages in 5-second intervals and tracks a 5-minute rolling average.
   - Strictly enforces a 5-minute warmup period before triggers can evaluate.
   - Baseline calculation strictly excludes the current spike.

3. **FFmpeg EBU R128 Audio Analysis**:
   - Measures the loudest 1-second interval (`ebur128` momentary loudness integration) for each 10-second chunk.
   - Computes a 5-minute rolling audio energy baseline.
   - Handles silence and missing audio streams gracefully (floors at -70.0 LUFS).

4. **Dual Trigger Detection & Slicing**:
   - Triggers when:
     - `Chat activity >= 3x rolling chat average`
     - **AND** `Audio loudness >= rolling baseline + 6 dB`
     - Both spikes occur within **15 seconds** of each other.
   - Waits 15 seconds for reaction capture.
   - Slices exactly **30 seconds before** and **15 seconds after** the event (45-second clip total).
   - Enforces a **120-second cooldown** after each clip.

5. **Vertical Video Rendering & Subtitle Burn**:
   - Renders 1080x1920 (9:16 vertical video).
   - Fast background blur: scales down source to 360x640, blurs, and smoothly scales back up to 1080x1920 with brightness darkening for 10x render speedup.
   - Centers original gameplay with un-stretched aspect ratio.
   - Uses `faster-whisper` for offline CPU transcription with word-level timestamps.
   - Groups 3 to 4 words per cue, formatted with large, bold text, white fill, thick black borders, and drop shadows burned into the final MP4.

6. **AI Title Generation & Metadata**:
   - Supports Gemini CLI (`gemini`) if installed.
   - Smart local heuristic fallback extracting hype sentiment keywords (`W`, `SIUUU`, `INSANE`, etc.) and transcript fragments.
   - Generates a title under 60 characters and exactly 3 hashtags.
   - Windows/POSIX-safe filename sanitization.
   - Writes matching `.txt` metadata file and logs `[CLIP CREATED] HH:MM:SS | Title | clips/filename.mp4`.

7. **Real-time Terminal Dashboard**:
   - Updates every 1 second in-place without log spam.
   - Displays all 14 required metrics (stream status, quality, buffer chunks, IRC connection, chat rates, audio measurements, warmup progress, cooldown, clips, errors, free disk space).

---

## Installation & Setup

### Prerequisites
- macOS (or Linux)
- Python 3.10+ (tested with Python 3.11.15)
- FFmpeg 6.0+

### Start the Clipper
Run with default channel (`ishowspeed`):
```bash
./start.sh
```

Or specify custom channel and options:
```bash
./start.sh --channel ishowspeed --quality 720p,720p60,best --warmup 300 --cooldown 120
```

### Dynamic Limit & Sensitivity Flags
```bash
# Auto-stop after generating 3 clips:
./start.sh --channel jynxzi --max-clips 3

# Auto-stop after 1 hour (3600 seconds):
./start.sh --channel jynxzi --max-duration 3600

# High-sensitivity mode (trigger on smaller spikes: 1.8x chat or +4dB audio):
./start.sh --channel jynxzi --chat-spike 1.8 --audio-spike 4.0

# Single-trigger mode (trigger if EITHER chat spikes OR audio spikes):
./start.sh --channel jynxzi --trigger-mode any
```

---

## Running the Test Suite

The test suite includes comprehensive unit tests and a full synthetic end-to-end integration test:

```bash
source .venv/bin/activate
export PYTHONPATH=".venv:$PYTHONPATH"
python3 -m unittest discover -s tests -v
```

All 18 tests pass:
- `test_irc_parser.py`: PRIVMSG extraction, user tagging, PING/PONG.
- `test_rolling_metrics.py`: 5s buckets, spike exclusion from baseline, warmup status.
- `test_trigger_engine.py`: Dual spike correlation within 15s, warmup guard, 120s cooldown.
- `test_buffer_and_reconstruction.py`: Retention cleanup, chunk locking, FFmpeg stitching.
- `test_sanitization_and_title.py`: Windows-safe characters, length < 60 chars, 3 hashtags.
- `test_video_and_captions.py`: Pillow subtitle generation, 1080x1920 dimensions.
- `test_e2e_synthetic.py`: Complete pipeline with synthetic video, audio spikes, chat simulation, faster-whisper transcription, subtitle burning, and MP4/TXT export.

