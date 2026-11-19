#!/usr/bin/env bash
# Command to manually stitch chunks from temp/ into a 40s+ vertical split clip
# Usage:
#   ./manual_clip.sh                  # Uses the last 5 chunks (~45-50s)
#   ./manual_clip.sh --duration 42    # Custom duration (minimum 40s)
#   ./manual_clip.sh --last 6         # Uses the last 6 chunks
#   ./manual_clip.sh --title "MY CLIP TITLE"

set -e
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_BIN="$PROJECT_DIR/live-clipper/.venv/bin/python"

if [ ! -f "$PYTHON_BIN" ]; then
    echo "Virtual environment not found. Please run setup first."
    exit 1
fi

"$PYTHON_BIN" "$PROJECT_DIR/manual_clip.py" "$@"

