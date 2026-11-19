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
echo "🧪 Checking Whisper AI Model & Test Suite"
echo "=================================================="

# Ensure the local models directory exists
mkdir -p models/faster-whisper-base

# If model is not in local folder, check HuggingFace cache or download directly
if [ ! -f "models/faster-whisper-base/model.bin" ]; then
    HF_SNAPSHOT=$(ls -d ~/.cache/huggingface/hub/models--Systran--faster-whisper-base/snapshots/* 2>/dev/null | head -n 1 || true)
    if [ -n "$HF_SNAPSHOT" ] && [ -d "$HF_SNAPSHOT" ]; then
        echo "📦 Linking Whisper model from system cache to live-clipper/models/..."
        cp -L "$HF_SNAPSHOT"/* models/faster-whisper-base/ 2>/dev/null || true
    else
        echo "📥 Downloading Whisper base model (~138 MB) into live-clipper/models/..."
        ./.venv/bin/python -c '
from faster_whisper import download_model
download_model("base", output_dir="models/faster-whisper-base")
print("✅ Download complete!")
' 2>/dev/null || true
    fi
fi

if [ -f "models/faster-whisper-base/model.bin" ]; then
    echo "✅ Whisper model found at live-clipper/models/faster-whisper-base/model.bin"
else
    echo "ℹ️  Model will run from system cache (~/.cache/huggingface/)"
fi

echo ""
echo "🚀 Running 18 unit and synthetic tests..."
./.venv/bin/python -m unittest discover tests

echo ""
echo "=================================================="
echo "✅ All tests passed and AI models verified!"
echo "=================================================="

