#!/usr/bin/env bash
# Quick launcher for Live Clipper (Supports Twitch and YouTube Live Streams)
# Splits output video into: Top Streamer Facecam & Bottom Gameplay Screen.

set -e
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_BIN="$PROJECT_DIR/live-clipper/.venv/bin/python"

if [ ! -f "$PYTHON_BIN" ]; then
    echo "Virtual environment not found. Please run setup first."
    exit 1
fi

export PYTHONPATH="$PROJECT_DIR/live-clipper:$PYTHONPATH"

# Run with passed arguments (defaulting to split layout)
"$PYTHON_BIN" -m live_clipper.app "$@"

