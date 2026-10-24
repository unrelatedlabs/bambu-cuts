#!/bin/bash
# Quick run script for Bambu Cuts server
#
# Usage:
#   ./run.sh                       # default profile, port 5425
#   ./run.sh mini                  # profile "mini", its own port (5426 for the
#                                  #   second profile) or the one in its config
#   ./run.sh mini 5500             # profile "mini" on a port you pick
#
# One instance per printer: run this in two terminals with two profiles.

set -e

PROFILE="${1:-}"
PORT="${2:-}"

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

ARGS=()
if [ -n "$PROFILE" ]; then ARGS+=(--profile "$PROFILE"); fi
if [ -n "$PORT" ]; then ARGS+=(--port "$PORT"); fi

# Run the server (it prints the URL it settled on)
echo "✂️ Bambu Cuts starting${PROFILE:+ [profile: $PROFILE]}..."
python -m bambucuts.cli server "${ARGS[@]}"
