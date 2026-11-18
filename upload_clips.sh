#!/bin/bash
# Single command to upload all prepared clips to YouTube
# Auto-attaches thumbnails, waits for video rendering, and spaces uploads by 5-7 mins.
# Automatically removes local video and thumbnail files after successful upload.

set -e
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_BIN="$PROJECT_DIR/live-clipper/.venv/bin/python"

if [ ! -f "$PYTHON_BIN" ]; then
    echo "Virtual environment not found. Please run setup first."
    exit 1
fi

"$PYTHON_BIN" "$PROJECT_DIR/upload_clips.py" "$@"

