#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

API_HOST="127.0.0.1"
API_PORT="${OPSMINEFLOW_API_PORT:-8765}"
WEB_HOST="127.0.0.1"
WEB_PORT="${OPSMINEFLOW_WEB_PORT:-5173}"
VENV_PYTHON="$ROOT_DIR/.venv/bin/python"

if [[ -x "$VENV_PYTHON" ]]; then
  PYTHON_BIN="$VENV_PYTHON"
else
  PYTHON_BIN="${PYTHON_BIN:-python3}"
fi

export PYTHONPATH="$ROOT_DIR/services/mining-core/src:$ROOT_DIR/services/local-api/src:$ROOT_DIR/packages/drawio-exporter/src:${PYTHONPATH:-}"
export OPSMINEFLOW_API_HOST="$API_HOST"
export OPSMINEFLOW_API_PORT="$API_PORT"

info() {
  printf '==> %s\n' "$1"
}

fail() {
  printf 'ERROR: %s\n' "$1" >&2
  exit 1
}

require_port() {
  local host="$1"
  local port="$2"
  "$PYTHON_BIN" - "$host" "$port" <<'PY'
import socket
import sys

host = sys.argv[1]
port = int(sys.argv[2])
with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
    sock.settimeout(0.2)
    if sock.connect_ex((host, port)) == 0:
        raise SystemExit(1)
PY
}

wait_for_api() {
  "$PYTHON_BIN" - "$API_HOST" "$API_PORT" <<'PY'
import json
import sys
import time
import urllib.request

host = sys.argv[1]
port = sys.argv[2]
scheme = "http"
url = f"{scheme}://{host}:{port}/health"
last_error = ""
for _ in range(40):
    try:
        with urllib.request.urlopen(url, timeout=0.5) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if payload.get("status") == "ok":
            raise SystemExit(0)
    except Exception as exc:  # pragma: no cover - shell smoke helper
        last_error = str(exc)
        time.sleep(0.25)
raise SystemExit(f"Local API did not become ready: {last_error}")
PY
}

cleanup() {
  if [[ "${API_PID:-}" != "" ]]; then
    kill "$API_PID" >/dev/null 2>&1 || true
    wait "$API_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

"$PYTHON_BIN" - <<'PY' || fail "Python packages are not installed. Run ./scripts/install_mac.sh first."
import importlib

for module in ("opsmineflow_mining", "opsmineflow_drawio", "opsmineflow_api"):
    importlib.import_module(module)
PY

require_port "$API_HOST" "$API_PORT" || fail "Port ${API_PORT} is already in use. Stop the other local API process or set OPSMINEFLOW_API_PORT."
if [[ -d apps/desktop/node_modules ]]; then
  require_port "$WEB_HOST" "$WEB_PORT" || fail "Port ${WEB_PORT} is already in use. Stop the other WebUI process or set OPSMINEFLOW_WEB_PORT."
else
  fail "Desktop dependencies are not installed. Run ./scripts/install_mac.sh first."
fi

info "Starting OpsMineFlow local API on ${API_HOST}:${API_PORT}"
"$PYTHON_BIN" -m opsmineflow_api &
API_PID=$!
wait_for_api

info "Starting OpsMineFlow WebUI on ${WEB_HOST}:${WEB_PORT}"
if [[ "${OPSMINEFLOW_NO_OPEN:-0}" != "1" && "$(uname -s)" == "Darwin" ]]; then
  WEB_URL_SCHEME="http"
  (sleep 2 && open "${WEB_URL_SCHEME}://${WEB_HOST}:${WEB_PORT}") >/dev/null 2>&1 &
fi

npm --prefix apps/desktop run dev -- --host "$WEB_HOST" --port "$WEB_PORT" --strictPort
