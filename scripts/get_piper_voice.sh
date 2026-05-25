#!/usr/bin/env bash
# Downloads a Piper TTS voice from rhasspy/piper-voices into ./models/.
#
# Default voice (en_US-amy-medium) is small + fast. Override:
#     LAFUFU_VOICE=en_US-lessac-medium ./scripts/get_piper_voice.sh

set -euo pipefail

VOICE="${LAFUFU_VOICE:-en_US-amy-medium}"
OUTDIR="models"

# voice naming "<lang>_<region>-<speaker>-<quality>"
LANG_FULL="${VOICE%%-*}"        # en_US
REST="${VOICE#*-}"              # amy-medium
SPEAKER="${REST%%-*}"           # amy
QUALITY="${REST#*-}"            # medium
LANG_GROUP="${LANG_FULL:0:2}"   # en

BASE="https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/$LANG_GROUP/$LANG_FULL/$SPEAKER/$QUALITY"

mkdir -p "$OUTDIR"
ONNX="$OUTDIR/$VOICE.onnx"
JSON="$OUTDIR/$VOICE.onnx.json"

if [ ! -f "$ONNX" ]; then
    echo "Downloading $VOICE.onnx ..."
    curl -fsSL -o "$ONNX" "$BASE/$VOICE.onnx"
else
    echo "Already present: $ONNX"
fi

if [ ! -f "$JSON" ]; then
    echo "Downloading $VOICE.onnx.json ..."
    curl -fsSL -o "$JSON" "$BASE/$VOICE.onnx.json"
fi

echo ""
echo "Voice ready at $ONNX"
echo "Set in your agent shell:"
echo "    export LAFUFU_PIPER_MODEL=\"$(realpath "$ONNX")\""
