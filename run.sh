#!/usr/bin/env bash
# Usage: ./run.sh /path/to/photos [--output-dir outputs] [--ui-only]
#
# Runs the clustering/scoring pipeline on the given folder,
# then launches the Streamlit curation UI.
# Pass --ui-only to skip the pipeline and launch the UI directly.

set -euo pipefail

if [ $# -lt 1 ]; then
    echo "Usage: ./run.sh <image-folder> [--output-dir outputs] [--ui-only]"
    echo ""
    echo "Example: ./run.sh ~/Photos/vacation"
    echo "         ./run.sh ~/Photos/vacation --ui-only"
    exit 1
fi

IMAGE_DIR="$1"
shift

if [ ! -d "$IMAGE_DIR" ]; then
    echo "Error: '$IMAGE_DIR' is not a directory"
    exit 1
fi

# Parse --output-dir and --ui-only; pass remaining args to pipeline
OUTPUT_DIR="outputs"
UI_ONLY=0
REMAINING_ARGS=()
while [[ $# -gt 0 ]]; do
    case "$1" in
        --output-dir)
            OUTPUT_DIR="$2"; shift 2 ;;
        --output-dir=*)
            OUTPUT_DIR="${1#*=}"; shift ;;
        --ui-only)
            UI_ONLY=1; shift ;;
        *)
            REMAINING_ARGS+=("$1"); shift ;;
    esac
done

export SIGHTREAD_OUTPUT_DIR="$OUTPUT_DIR"

echo "📸 Sightread — Photo Curation"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

if [ "$UI_ONLY" -eq 0 ]; then
    echo "⚙️  Running pipeline on: $IMAGE_DIR"
    python scripts/pipeline.py --image-dir "$IMAGE_DIR" --output-dir "$OUTPUT_DIR" "${REMAINING_ARGS[@]+"${REMAINING_ARGS[@]}"}"
    echo ""
fi

echo "🚀 Launching curation UI..."
PYTHONPATH="$(pwd)/ui:$(pwd)/scripts${PYTHONPATH:+:$PYTHONPATH}" streamlit run ui/app.py --server.address 127.0.0.1
