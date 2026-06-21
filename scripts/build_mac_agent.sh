#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SOURCE="$ROOT_DIR/mac-agent/Sources/OpsMineFlowAgent/main.swift"
OUTPUT_DIR="$ROOT_DIR/mac-agent/bin"
OUTPUT="$OUTPUT_DIR/opsmineflow-agent"

if [[ "$(uname -s)" != "Darwin" ]]; then
  printf 'macOS agent build skipped outside macOS.\n'
  exit 0
fi

if ! command -v swiftc >/dev/null 2>&1; then
  printf 'ERROR: swiftc was not found. Install Xcode Command Line Tools first.\n' >&2
  exit 1
fi

if [[ "${1:-}" == "--check" ]]; then
  swiftc -typecheck -framework AppKit "$SOURCE"
  printf 'macOS agent Swift check passed.\n'
  exit 0
fi

mkdir -p "$OUTPUT_DIR"
swiftc -O -framework AppKit "$SOURCE" -o "$OUTPUT"
chmod 755 "$OUTPUT"
printf 'Built macOS agent: %s\n' "$OUTPUT"
