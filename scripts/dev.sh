#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

export PYTHONPATH="$ROOT_DIR/services/mining-core/src:$ROOT_DIR/services/local-api/src:${PYTHONPATH:-}"

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

