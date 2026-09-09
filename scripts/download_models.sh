#!/bin/sh
# Download the official openWakeWord v0.5.1 ONNX models into ./models
# and verify them against SHA256SUMS.
#
# The classifier model is "hey_jarvis" (English wake phrase "Hey Jarvis").
# To use another wake phrase, fetch its release assets the same way or
# train a custom openWakeWord model and drop the .onnx files in ./models
# (any *.onnx that is not melspectrogram/embedding_model is treated as
# the wake-word classifier).
set -eu

BASE_URL="https://github.com/dscripka/openWakeWord/releases/download/v0.5.1"
FILES="hey_jarvis_v0.1.onnx embedding_model.onnx melspectrogram.onnx"
DEST="$(dirname "$0")/../models"

mkdir -p "$DEST"
for f in $FILES; do
    if [ -f "$DEST/$f" ]; then
        echo "exists: $f"
    else
        echo "downloading: $f"
        curl -fL --retry 3 -o "$DEST/$f" "$BASE_URL/$f"
    fi
done

(cd "$DEST" && sha256sum -c SHA256SUMS)
echo "OK: models ready in $DEST"
