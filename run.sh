#!/usr/bin/env bash
# Usage: ./run.sh /path/to/photos [--output-dir outputs]
#
# Runs the clustering/scoring pipeline on the given folder,
# then launches the Streamlit curation UI.

set -euo pipefail

if [ $# -lt 1 ]; then
    echo "Usage: ./run.sh <image-folder> [--output-dir outputs]"
    echo ""
    echo "Example: ./run.sh ~/Photos/vacation"
    exit 1
fi

IMAGE_DIR="$1"
shift

if [ ! -d "$IMAGE_DIR" ]; then
    echo "Error: '$IMAGE_DIR' is not a directory"
    exit 1
fi

echo "📸 Sightread — Photo Curation"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

echo "⚙️  Running pipeline on: $IMAGE_DIR"
python scripts/pipeline.py --image-dir "$IMAGE_DIR" "$@"

echo ""
echo "🚀 Launching curation UI..."
streamlit run ui/app.py
