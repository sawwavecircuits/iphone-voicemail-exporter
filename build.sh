#!/usr/bin/env bash
# Build voicemail_exporter as a standalone macOS/Linux binary using PyInstaller.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "==> Checking for PyInstaller..."
if ! python -m PyInstaller --version &>/dev/null; then
    echo "Installing PyInstaller..."
    pip install pyinstaller
fi

echo "==> Building voicemail_exporter..."
python -m PyInstaller \
    --onefile \
    --name voicemail_exporter \
    --strip \
    voicemail_exporter.py

echo ""
echo "Build complete! Binary is at: dist/voicemail_exporter"
echo ""
echo "Usage:"
echo "  ./dist/voicemail_exporter --help"
echo "  ./dist/voicemail_exporter --output ./my_voicemails"
