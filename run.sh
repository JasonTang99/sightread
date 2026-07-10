#!/usr/bin/env bash
# Usage: ./run.sh [/path/to/photos] [--ui-only]
#
# Without arguments: launches the webapp and opens the browser for folder selection.
# With a folder: runs the pipeline on that folder first, then launches the webapp.
# Pass --ui-only to skip the pipeline and launch the UI directly.

set -euo pipefail

IMAGE_DIR=""
UI_ONLY=0
REMAINING_ARGS=()

while [[ $# -gt 0 ]]; do
    case "$1" in
        --ui-only)
            UI_ONLY=1; shift ;;
        -*)
            REMAINING_ARGS+=("$1"); shift ;;
        *)
            if [ -z "$IMAGE_DIR" ]; then
                IMAGE_DIR="$1"
            else
                REMAINING_ARGS+=("$1")
            fi
            shift ;;
    esac
done

if [ -n "$IMAGE_DIR" ] && [ ! -d "$IMAGE_DIR" ]; then
    echo "Error: '$IMAGE_DIR' is not a directory"
    exit 1
fi

echo "📸 Sightread — Photo Curation"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

if [ -n "$IMAGE_DIR" ] && [ "$UI_ONLY" -eq 0 ]; then
    echo "⚙️  Running pipeline on: $IMAGE_DIR"
    python scripts/pipeline.py --image-dir "$IMAGE_DIR" "${REMAINING_ARGS[@]+"${REMAINING_ARGS[@]}"}"
    echo ""
fi

if [ ! -f webapp/frontend/dist/index.html ]; then
    echo "🔨 Building frontend (dist missing)..."
    (cd webapp/frontend && npm install && npm run build)
fi

URL="http://127.0.0.1:8765"
echo "🚀 Launching curation UI at $URL ..."

# Open browser after server has a moment to bind
(sleep 1 && xdg-open "$URL" 2>/dev/null || open "$URL" 2>/dev/null || true) &

# No --reload: it restarts the server on file edits, wiping the active
# project and undo stack (both held in memory).
cd webapp && python -m uvicorn server:app --host 127.0.0.1 --port 8765
