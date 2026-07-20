#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DESKTOP_DIR="$ROOT_DIR/apps/desktop"
PYTHON_BIN="$ROOT_DIR/.venv/bin/python"

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "ERROR: Missing $PYTHON_BIN. Run ./scripts/install_mac.sh first." >&2
  exit 1
fi

export OPSMINEFLOW_DEV_SIDECAR="$PYTHON_BIN"
export OPSMINEFLOW_DEV_PYTHONPATH="$ROOT_DIR/services/mining-core/src:$ROOT_DIR/services/local-api/src:$ROOT_DIR/packages/drawio-exporter/src"

cd "$ROOT_DIR"
npm --prefix "$DESKTOP_DIR" run tauri -- dev
