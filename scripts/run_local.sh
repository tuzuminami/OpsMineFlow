#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

API_HOST="127.0.0.1"
API_PORT="${OPSMINEFLOW_API_PORT:-8765}"
WEB_HOST="127.0.0.1"
WEB_PORT="${OPSMINEFLOW_WEB_PORT:-5173}"
LOCAL_HTTP_SCHEME="http"
VENV_PYTHON="$ROOT_DIR/.venv/bin/python"

if [[ -x "$VENV_PYTHON" ]]; then
  PYTHON_BIN="$VENV_PYTHON"
else
  PYTHON_BIN="${PYTHON_BIN:-python3}"
fi

export PYTHONPATH="$ROOT_DIR/services/mining-core/src:$ROOT_DIR/services/local-api/src:$ROOT_DIR/packages/drawio-exporter/src:${PYTHONPATH:-}"
export OPSMINEFLOW_API_HOST="$API_HOST"
export OPSMINEFLOW_API_PORT="$API_PORT"
export OPSMINEFLOW_WEBUI_PORT="$WEB_PORT"
export VITE_API_BASE="${LOCAL_HTTP_SCHEME}://${API_HOST}:${API_PORT}"

info() {
  printf '==> %s\n' "$1"
}

fail() {
  printf 'ERROR: %s\n' "$1" >&2
  exit 1
}

port_is_open() {
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
        raise SystemExit(0)
raise SystemExit(1)
PY
}

api_is_opsmineflow() {
  "$PYTHON_BIN" - "$API_HOST" "$API_PORT" <<'PY'
import json
import sys
import time
import urllib.request

host = sys.argv[1]
port = sys.argv[2]
scheme = "http"
url = f"{scheme}://{host}:{port}/health"
for _ in range(8):
    try:
        with urllib.request.urlopen(url, timeout=0.5) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if payload.get("status") == "ok" and payload.get("local_only") is True:
            raise SystemExit(0)
    except Exception:  # pragma: no cover - shell smoke helper
        time.sleep(0.25)
raise SystemExit(1)
PY
}

webui_is_opsmineflow() {
  "$PYTHON_BIN" - "$WEB_HOST" "$WEB_PORT" <<'PY'
import sys
import time
import urllib.request

host = sys.argv[1]
port = sys.argv[2]
scheme = "http"
url = f"{scheme}://{host}:{port}"
for _ in range(8):
    try:
        with urllib.request.urlopen(url, timeout=0.5) as response:
            html = response.read().decode("utf-8", errors="replace")
        if "<title>OpsMineFlow</title>" in html:
            raise SystemExit(0)
    except Exception:  # pragma: no cover - shell smoke helper
        time.sleep(0.25)
raise SystemExit(1)
PY
}

wait_for_api() {
  for _ in {1..20}; do
    if api_is_opsmineflow; then
      return 0
    fi
    sleep 0.25
  done
  return 1
}

open_webui() {
  if [[ "${OPSMINEFLOW_NO_OPEN:-0}" != "1" && "$(uname -s)" == "Darwin" ]]; then
    open "${LOCAL_HTTP_SCHEME}://${WEB_HOST}:${WEB_PORT}" >/dev/null 2>&1 || true
  fi
}

print_stop_command() {
  printf 'Stop OpsMineFlow with:\n  cd "%s" && ./scripts/stop_local.sh\n' "$ROOT_DIR"
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

if [[ ! -d apps/desktop/node_modules ]]; then
  fail "Desktop dependencies are not installed. Run ./scripts/install_mac.sh first."
fi

API_REUSED=0
WEB_REUSED=0

if port_is_open "$API_HOST" "$API_PORT"; then
  if api_is_opsmineflow; then
    API_REUSED=1
    info "OpsMineFlow local API is already running on ${API_HOST}:${API_PORT}"
  else
    fail "Port ${API_PORT} is used by another program. Inspect it with: lsof -nP -iTCP:${API_PORT} -sTCP:LISTEN"
  fi
fi

if port_is_open "$WEB_HOST" "$WEB_PORT"; then
  if webui_is_opsmineflow; then
    WEB_REUSED=1
    info "OpsMineFlow WebUI is already running on ${WEB_HOST}:${WEB_PORT}"
  else
    fail "Port ${WEB_PORT} is used by another program. Inspect it with: lsof -nP -iTCP:${WEB_PORT} -sTCP:LISTEN"
  fi
fi

if [[ "$API_REUSED" == "1" && "$WEB_REUSED" == "1" ]]; then
  info "OpsMineFlow is ready at ${LOCAL_HTTP_SCHEME}://${WEB_HOST}:${WEB_PORT}"
  open_webui
  print_stop_command
  exit 0
fi

if [[ "$API_REUSED" == "0" ]]; then
  info "Starting OpsMineFlow local API on ${API_HOST}:${API_PORT}"
  "$PYTHON_BIN" -m opsmineflow_api &
  API_PID=$!
  wait_for_api || fail "Local API did not become ready. Review the terminal output above."
fi

if [[ "$WEB_REUSED" == "1" ]]; then
  info "OpsMineFlow is ready at ${LOCAL_HTTP_SCHEME}://${WEB_HOST}:${WEB_PORT}"
  open_webui
  print_stop_command
  wait "$API_PID"
  exit 0
fi

info "Starting OpsMineFlow WebUI on ${WEB_HOST}:${WEB_PORT}"
(sleep 2 && open_webui) &
print_stop_command
npm --prefix apps/desktop run dev -- --host "$WEB_HOST" --port "$WEB_PORT" --strictPort
