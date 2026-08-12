#!/usr/bin/env bash
# Fetch the Mozart OMR engine (github.com/aashrafh/Mozart, Apache-2.0).
#
# Mozart is not vendored into this repository: it is a separate project with its
# own history, and pinning a checkout keeps the two independent. This script
# clones it (or updates an existing clone) into third_party/mozart, which is
# where the `mozart` engine looks by default.
#
# Usage: scripts/fetch_mozart.sh [target-directory]
set -euo pipefail

REPO="https://github.com/aashrafh/Mozart.git"
TARGET="${1:-third_party/mozart}"

if [ -d "$TARGET/.git" ]; then
  echo "==> updating existing checkout in $TARGET"
  git -C "$TARGET" fetch --depth 1 origin
  git -C "$TARGET" reset --hard origin/HEAD
else
  echo "==> cloning Mozart into $TARGET"
  mkdir -p "$(dirname "$TARGET")"
  git clone --depth 1 "$REPO" "$TARGET"
fi

MODEL="$TARGET/src/trained_models/nn_trained_model_hog.sav"
if [ ! -f "$MODEL" ]; then
  echo "error: the pretrained classifier is missing at $MODEL" >&2
  echo "       Mozart cannot classify glyphs without it." >&2
  exit 1
fi

cat <<EOF

Mozart is in $TARGET

Install its Python dependencies into the same environment as transposer:

    pip install 'transposer[mozart]'

Then check that the engine reports itself as usable:

    TRANSPOSER_MOZART_DIR=$TARGET transposer engines
EOF
