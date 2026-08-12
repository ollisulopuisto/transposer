#!/usr/bin/env bash
# Build Audiveris from source and install it under third_party/audiveris.
#
# Audiveris is the engine to use for real scans: it reads PDFs directly, handles
# grand staves, and recovers chord symbols and lyrics. There is no PyPI package
# and no official Linux binary for every distribution, so building from source is
# the reliable route.
#
# Requirements: git, and a JDK matching the version the checkout asks for
# (gradle.properties -> theMinJavaVersion). Everything else the Gradle wrapper
# fetches, including a bundled Tesseract via the bytedeco packages.
#
# Usage: scripts/install_audiveris.sh [tag-or-branch] [target-directory]
set -euo pipefail

REF="${1:-development}"
TARGET="${2:-third_party/audiveris}"
REPO="https://github.com/Audiveris/audiveris.git"

if [ -d "$TARGET/.git" ]; then
  echo "==> updating $TARGET"
  git -C "$TARGET" fetch --tags origin
  git -C "$TARGET" checkout "$REF"
else
  echo "==> cloning Audiveris ($REF) into $TARGET"
  mkdir -p "$(dirname "$TARGET")"
  git clone "$REPO" "$TARGET"
  git -C "$TARGET" checkout "$REF"
fi

REQUIRED_JAVA="$(sed -n 's/^theMinJavaVersion *= *\([0-9]*\).*/\1/p' "$TARGET/gradle.properties")"
echo "==> this checkout needs JDK ${REQUIRED_JAVA:-?}"

if [ -n "${JAVA_HOME:-}" ]; then
  GRADLE_JAVA_ARG="-Dorg.gradle.java.home=$JAVA_HOME"
  echo "==> building with JAVA_HOME=$JAVA_HOME"
else
  GRADLE_JAVA_ARG=""
  echo "==> building with the default java on PATH"
fi

echo "==> building (this takes a few minutes on a first run)"
(cd "$TARGET" && ./gradlew --no-daemon $GRADLE_JAVA_ARG installDist)

LAUNCHER="$(cd "$TARGET" && ls app/build/install/*/bin/Audiveris | head -1)"
LAUNCHER="$(cd "$TARGET" && realpath "$LAUNCHER")"

cat <<EOF

Audiveris is built.

    export TRANSPOSER_AUDIVERIS="$LAUNCHER"
$( [ -n "${JAVA_HOME:-}" ] && echo "    export TRANSPOSER_JAVA_HOME=\"$JAVA_HOME\"" )

Lyrics and chord names additionally need Tesseract language data that includes
the *legacy* engine -- the tessdata_fast files most distributions ship do not,
and Audiveris initialises Tesseract in legacy mode:

    mkdir -p third_party/tessdata
    curl -Lo third_party/tessdata/eng.traineddata \\
      https://raw.githubusercontent.com/tesseract-ocr/tessdata/main/eng.traineddata
    export TESSDATA_PREFIX="\$PWD/third_party/tessdata"

Then:

    transposer engines
EOF
