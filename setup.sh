#!/bin/bash
set -e

DIR="/Users/ryanwong/App Ideas/video-analyzer"
cd "$DIR"

echo "Checking ffmpeg..."
if ! command -v ffmpeg &>/dev/null; then
    echo "ffmpeg not found. Install with: brew install ffmpeg"
    exit 1
fi

echo "Checking claude CLI..."
if ! command -v claude &>/dev/null; then
    echo "claude CLI not found. Install Claude Code first."
    exit 1
fi

echo "Creating virtualenv..."
python3 -m venv venv
source venv/bin/activate
pip install -q -r requirements.txt

echo ""
echo "Setup complete."
echo "Run the dashboard:"
echo "  cd '$DIR' && source venv/bin/activate && streamlit run app.py"
