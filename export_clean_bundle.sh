#!/usr/bin/env bash
# export_clean_bundle.sh
# Packages the entire Anti-Clips codebase into a clean, lightweight zip archive
# ready to copy to another desktop. Excludes credentials, venv, and temporary media.

set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

TMP_ZIP="/tmp/Anti-Clips-Source.zip"
FINAL_ZIP="$DIR/Anti-Clips-Source.zip"
rm -f "$TMP_ZIP" "$FINAL_ZIP"

echo "Creating clean code bundle: $FINAL_ZIP..."

zip -r "$TMP_ZIP" . \
    -x "*.venv*" \
    -x "*__pycache__*" \
    -x "*.git*" \
    -x "live-clipper/temp/*" \
    -x "live-clipper/clips/*" \
    -x "live-clipper/models/*" \
    -x "*.log*" \
    -x "client_secret.json" \
    -x "youtube_token.json" \
    -x "*.DS_Store" \
    -x "*.zip"

mv "$TMP_ZIP" "$FINAL_ZIP"

echo ""
echo "✅ Export complete! File created at: $FINAL_ZIP"
echo "You can transfer Anti-Clips-Source.zip to any other desktop and extract it."
echo "Follow the instructions in HANDOFF_SETUP.md on the new machine."
