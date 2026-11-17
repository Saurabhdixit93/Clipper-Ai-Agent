#!/usr/bin/env bash
set -e

# Change to script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "=== Starting Twitch Live Clipper ==="

# Check virtual environment
if [ ! -d ".venv" ]; then
    echo "Creating virtual environment with python3.11..."
    python3.11 -m venv .venv || python3 -m venv .venv
    ./.venv/bin/pip install --upgrade pip
    ./.venv/bin/pip install -r requirements.txt
fi

# Activate virtual environment
source .venv/bin/activate
export PYTHONPATH="$SCRIPT_DIR:$PYTHONPATH"

# Pass any CLI arguments to app.py
python3 -m live_clipper.app "$@"

