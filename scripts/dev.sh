#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

export PYTHONPATH="$ROOT_DIR/services/mining-core/src:$ROOT_DIR/services/local-api/src:$ROOT_DIR/packages/drawio-exporter/src:${PYTHONPATH:-}"
export VITE_API_BASE="${VITE_API_BASE:-http://127.0.0.1:8765}"

if [[ "${OPSMINEFLOW_INSECURE_BROWSER_DEV_API:-}" != "1" ]]; then
  echo "ERROR: dev.sh is an insecure browser-development helper. Use ./scripts/dev_desktop.sh for the managed desktop runtime, or set OPSMINEFLOW_INSECURE_BROWSER_DEV_API=1 only with disposable test data." >&2
  exit 1
fi

export OPSMINEFLOW_INSECURE_BROWSER_DEV_API=1

echo "Starting OpsMineFlow local API on http://127.0.0.1:8765"
python3 -m opsmineflow_api &
API_PID=$!

cleanup() {
  kill "$API_PID" >/dev/null 2>&1 || true
}
trap cleanup EXIT

if [[ -f apps/desktop/package.json && -d apps/desktop/node_modules ]]; then
  npm --prefix apps/desktop run dev
else
  echo "Desktop dependencies are not installed. API is running; press Ctrl+C to stop."
  wait "$API_PID"
fi
