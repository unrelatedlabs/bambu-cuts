#!/bin/bash
# Quick run script for Bambu Cuts server

echo "🚀 Starting Bambu Cuts Server..."

# Check if venv exists
if [ ! -d ".venv" ]; then
    echo "📦 Creating virtual environment..."
    python3 -m venv .venv
fi

# Activate venv
source .venv/bin/activate

# Check if dependencies are installed
if ! python -c "import flask" 2>/dev/null; then
    echo "📥 Installing dependencies..."
    pip install -r requirements.txt
fi

# Run the server
echo "✂️ Bambu Cuts starting at http://localhost:5425"
python -m bambucuts.webui.app
