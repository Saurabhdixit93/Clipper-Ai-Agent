#!/bin/bash
# Single command to process any new clips in clips/
# Generates viral thumbnails, titles, descriptions, and tags.

set -e
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_BIN="$PROJECT_DIR/live-clipper/.venv/bin/python"

if [ ! -f "$PYTHON_BIN" ]; then
    echo "Virtual environment not found. Please run setup first."
    exit 1
fi

"$PYTHON_BIN" "$PROJECT_DIR/process_clips.py"

