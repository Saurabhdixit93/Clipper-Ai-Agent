#!/usr/bin/env bash
# start_test.sh
# Runs the full Anti-Clips test suite (18 unit & synthetic integration tests)
# Also automatically verifies and downloads the Faster-Whisper AI model on first run.

set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR/live-clipper"

if [ ! -d ".venv" ]; then
    echo "❌ Error: Virtual environment (.venv) not found in live-clipper/."
    echo "Please run: python3.11 -m venv live-clipper/.venv && ./live-clipper/.venv/bin/pip install -r live-clipper/requirements.txt"
    exit 1
fi

echo "=================================================="
echo "🧪 Running Anti-Clips Test Suite & Model Verification"
echo "=================================================="

./.venv/bin/python -m unittest discover tests

echo ""
echo "=================================================="
echo "✅ All tests passed and AI models verified!"
echo "=================================================="
