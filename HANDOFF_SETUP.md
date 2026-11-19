# 🚀 Anti-Clips: Complete End-to-End Setup & Handoff Guide

This document is a self-contained blueprint to replicate the entire **Anti-Clips** automation pipeline on any new desktop running **Antigravity IDE** (or terminal) from scratch without needing to copy the existing virtual environment, temporary chunks, or video cache.

---

## 📋 The Copy-Paste Setup Prompt for Antigravity IDE
> **Instructions for the new desktop:** Open Antigravity IDE on the new computer, open an empty workspace folder (e.g. `Anti-Clips`), and paste the prompt below into the chat:

```markdown
You are setting up the Anti-Clips system in this workspace. 

Anti-Clips is an automated AI live-stream clipping, vertical video editing, viral thumbnail generation, and YouTube upload system.

Follow these setup steps:
1. Ensure system prerequisites are present: Python 3.11+, FFmpeg (with libx264 and aac support), and git.
2. Initialize the project structure:
   - live-clipper/
     - live_clipper/ (core Python modules)
     - tests/ (unit and E2E synthetic tests)
     - clips/ (output directory for finished clips)
     - temp/ (rolling buffer for stream chunks)
     - requirements.txt
   - Helper scripts in workspace root:
     - record_stream.sh
     - process_clips.sh & process_clips.py
     - upload_clips.sh & upload_clips.py
     - manual_clip.sh & manual_clip.py
3. Create a dedicated Python 3.11 virtual environment at `live-clipper/.venv`.
4. Install all dependencies from requirements.txt:
   - streamlink, faster-whisper, requests, pillow, numpy, google-api-python-client, google-auth, google-auth-oauthlib, google-auth-httplib2.
5. Setup the vertical split layout:
   - Top half (1080x960): Streamer facecam box zoomed and centered (crop: iw*0.21:ih*0.23:0:ih*0.41).
   - Bottom half (1080x960): Gameplay scene offset and zoomed (crop: iw*0.76:ih:iw*0.22:0) to hide streamer box and minimap.
   - Yellow split divider line.
6. Check for `client_secret.json` and `youtube_token.json` in the workspace root for YouTube uploads.
7. Run the test suite: `cd live-clipper && ./.venv/bin/python -m unittest discover tests` to verify all 18 tests pass.
```

---

## 🛠️ Step-by-Step Manual Setup (Terminal)

### 1. System Prerequisites
Ensure the machine has **Python 3.11+**, **FFmpeg**, and **Git**:

- **macOS (Homebrew):**
  ```bash
  brew install python@3.11 ffmpeg git
  ```
- **Ubuntu / Debian:**
  ```bash
  sudo apt update && sudo apt install -y python3.11 python3.11-venv ffmpeg git
  ```

---

### 2. Virtual Environment & Dependencies
In the workspace root directory:

```bash
mkdir -p live-clipper/clips live-clipper/temp

# Create virtualenv inside live-clipper
python3.11 -m venv live-clipper/.venv

# Upgrade pip and install all required packages
./live-clipper/.venv/bin/pip install --upgrade pip
./live-clipper/.venv/bin/pip install \
    "streamlink>=8.6.3" \
    "faster-whisper>=1.2.1" \
    "requests>=2.34.2" \
    "pillow>=13.0.0" \
    "numpy>=2.0.0" \
    "google-api-python-client>=2.200.0" \
    "google-auth>=2.35.0" \
    "google-auth-oauthlib>=1.2.0" \
    "google-auth-httplib2>=0.2.0"
```

---

### 3. Architecture & File Structure

```text
Anti-Clips/
├── HANDOFF_SETUP.md            <-- This guide
├── client_secret.json          <-- Google Cloud OAuth client credentials (machine-specific)
├── youtube_token.json          <-- Saved YouTube OAuth refresh token (machine-specific)
├── record_stream.sh            <-- Main stream recorder & auto-clipper
├── manual_clip.sh              <-- Manual on-demand 40s+ clip generator
├── manual_clip.py              <-- Backend logic for manual clipping
├── process_clips.sh            <-- Viral thumbnail & SEO metadata processor
├── process_clips.py            <-- Viral thumbnail design & tags generator
├── upload_clips.sh             <-- YouTube staggered uploader with auto-cleanup
├── upload_clips.py             <-- YouTube Data API uploader with 6-min gaps
└── live-clipper/
    ├── requirements.txt
    ├── .venv/                  <-- Python 3.11 virtual environment
    ├── clips/                  <-- Output directory for completed clips
    ├── temp/                   <-- Rolling 10-minute buffer chunks
    ├── live_clipper/
    │   ├── __init__.py
    │   ├── config.py           <-- Layout, timing, and stream configurations
    │   ├── app.py              <-- CLI orchestrator and event loop
    │   ├── recorder.py         <-- Streamlink process & HLS stream capture
    │   ├── buffer_manager.py   <-- Rolling buffer chunking and cleanup
    │   ├── audio_analyzer.py   <-- FFmpeg volumedetect audio spike monitor
    │   ├── chat_monitor.py     <-- Twitch IRC chat spike monitor
    │   ├── trigger_engine.py   <-- Dual/audio/chat threshold detector
    │   ├── transcriber.py      <-- Faster-Whisper local speech-to-text
    │   ├── title_generator.py  <-- Viral title and hook generator
    │   ├── video_processor.py  <-- 1080x1920 split-screen FFmpeg engine
    │   ├── worker.py           <-- Async clip assembly worker queue
    │   └── dashboard.py        <-- Live terminal status UI
    └── tests/
        ├── test_buffer_and_reconstruction.py
        ├── test_e2e_synthetic.py
        ├── test_irc_parser.py
        ├── test_rolling_metrics.py
        ├── test_sanitization_and_title.py
        ├── test_trigger_engine.py
        └── test_video_and_captions.py
```

---

### 4. YouTube OAuth Credentials Setup

The YouTube credentials are the only files that should **not** be shared across public repositories.

1. **Google Cloud Console Setup:**
   - Go to [Google Cloud Console](https://console.cloud.google.com/).
   - Enable **YouTube Data API v3**.
   - Create an **OAuth 2.0 Client ID** (Desktop Application).
   - Download the JSON file and save it as `client_secret.json` in the workspace root.

2. **Generate `youtube_token.json`:**
   - Run the initial authorization script (or run `./upload_clips.sh`).
   - If running on a desktop with a browser, a login prompt will open asking you to select your YouTube channel and authorize access.
   - The token will automatically be saved to `youtube_token.json` and refreshes silently on future uploads.

---

### 5. Running the Pipeline

#### Command A: Record a Live Stream (YouTube or Twitch)
Records the stream into chunks, detects hype/loud moments automatically, and renders split-screen 40s+ clips into `clips/`:
```bash
# YouTube Stream (e.g. IShowSpeed):
./record_stream.sh --url "https://www.youtube.com/live/VIDEO_ID" --facecam speed

# Twitch Stream:
./record_stream.sh --channel ishowspeed --facecam speed
```

#### Command B: Create a Manual Clip On Demand
If you already have chunks recorded in `temp/` and want an instant 45-second vertical clip:
```bash
./manual_clip.sh --last 5 --duration 45 --facecam speed
```

#### Command C: Generate Viral Thumbnails & SEO Metadata
Scans `clips/` for newly created clips, draws high-CTR 16:9 & 9:16 thumbnails with bold fonts, contrast outlines, and focus arrows, and writes tags & descriptions:
```bash
./process_clips.sh
```

#### Command D: Upload to YouTube with 6-Minute Publication Gaps
Uploads all prepared clips to your YouTube channel, waits for transcode processing, assigns thumbnails, staggers uploads by 6 minutes, and automatically removes uploaded files from disk:
```bash
./upload_clips.sh
```

---

### 6. Verification & Self-Test
To verify that everything is installed and functioning properly on the new machine:
```bash
cd live-clipper
./.venv/bin/python -m unittest discover tests
```
*Expected Output:*
```text
Ran 18 tests in ~30s
OK
```
