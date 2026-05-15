#!/usr/bin/env sh
set -eu

TARGET="."
REPO="yxhpy/cc-router-codex"
REF="main"
YES=""

while [ "$#" -gt 0 ]; do
  case "$1" in
    --target)
      TARGET="$2"
      shift 2
      ;;
    --repo)
      REPO="$2"
      shift 2
      ;;
    --ref)
      REF="$2"
      shift 2
      ;;
    -y|--yes)
      YES="-y"
      shift
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

find_python() {
  for name in python3 python py; do
    if command -v "$name" >/dev/null 2>&1; then
      command -v "$name"
      return 0
    fi
  done
  echo "Python was not found on PATH. Install Python 3.11+ and rerun this installer." >&2
  return 1
}

PYTHON="$(find_python)"
mkdir -p "$TARGET"
TARGET_ABS="$(cd "$TARGET" && pwd)"
TMPDIR_ROOT="${TMPDIR:-/tmp}"
WORKDIR="$(mktemp -d "$TMPDIR_ROOT/cc-router-codex.XXXXXX")"
ARCHIVE="$WORKDIR/source.zip"
EXTRACT="$WORKDIR/extract"
URL="https://github.com/$REPO/archive/refs/heads/$REF.zip"

cleanup() {
  rm -rf "$WORKDIR"
}
trap cleanup EXIT INT TERM

mkdir -p "$EXTRACT"
echo "Downloading $URL"
if command -v curl >/dev/null 2>&1; then
  curl -fsSL "$URL" -o "$ARCHIVE"
elif command -v wget >/dev/null 2>&1; then
  wget -qO "$ARCHIVE" "$URL"
else
  echo "curl or wget is required to download the installer archive." >&2
  exit 1
fi

if command -v unzip >/dev/null 2>&1; then
  unzip -q "$ARCHIVE" -d "$EXTRACT"
else
  "$PYTHON" -m zipfile -e "$ARCHIVE" "$EXTRACT"
fi

SOURCE_DIR="$(find "$EXTRACT" -mindepth 1 -maxdepth 1 -type d | head -n 1)"
if [ -z "$SOURCE_DIR" ]; then
  echo "Unable to locate extracted repository directory." >&2
  exit 1
fi

"$PYTHON" "$SOURCE_DIR/install.py" --target "$TARGET_ABS" ${YES}
