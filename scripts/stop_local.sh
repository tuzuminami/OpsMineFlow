#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
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

info() {
  printf '==> %s\n' "$1"
}

fail() {
  printf 'ERROR: %s\n' "$1" >&2
  exit 1
}

need_command() {
  command -v "$1" >/dev/null 2>&1 || fail "$1 was not found."
}

port_is_open() {
  local host="$1"
  local port="$2"
  "$PYTHON_BIN" - "$host" "$port" <<'PY'
import socket
import sys

with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
    sock.settimeout(0.2)
    raise SystemExit(0 if sock.connect_ex((sys.argv[1], int(sys.argv[2]))) == 0 else 1)
PY
}

api_is_opsmineflow() {
  "$PYTHON_BIN" - "$API_HOST" "$API_PORT" <<'PY'
import json
import sys
import urllib.request

try:
    scheme = "http"
    with urllib.request.urlopen(f"{scheme}://{sys.argv[1]}:{sys.argv[2]}/health", timeout=1) as response:
        payload = json.loads(response.read().decode("utf-8"))
except Exception:
    raise SystemExit(1)
raise SystemExit(0 if payload.get("status") == "ok" and payload.get("local_only") is True else 1)
PY
}

webui_is_opsmineflow() {
  "$PYTHON_BIN" - "$WEB_HOST" "$WEB_PORT" <<'PY'
import sys
import urllib.request

try:
    scheme = "http"
    with urllib.request.urlopen(f"{scheme}://{sys.argv[1]}:{sys.argv[2]}", timeout=1) as response:
        html = response.read().decode("utf-8", errors="replace")
except Exception:
    raise SystemExit(1)
raise SystemExit(0 if "<title>OpsMineFlow</title>" in html else 1)
PY
}

pids_for_port() {
  lsof -tiTCP:"$1" -sTCP:LISTEN 2>/dev/null | sort -u || true
}

wait_for_closed_port() {
  local host="$1"
  local port="$2"
  for _ in {1..20}; do
    if ! port_is_open "$host" "$port"; then
      return 0
    fi
    sleep 0.25
  done
  return 1
}

need_command "$PYTHON_BIN"
need_command lsof

API_PIDS=""
WEB_PIDS=""
UNRECOGNIZED=0

if port_is_open "$API_HOST" "$API_PORT"; then
  if api_is_opsmineflow; then
    API_PIDS="$(pids_for_port "$API_PORT")"
  else
    printf 'ERROR: Port %s is not an OpsMineFlow API. It was left running.\n' "$API_PORT" >&2
    UNRECOGNIZED=1
  fi
fi

if port_is_open "$WEB_HOST" "$WEB_PORT"; then
  if webui_is_opsmineflow; then
    WEB_PIDS="$(pids_for_port "$WEB_PORT")"
  else
    printf 'ERROR: Port %s is not an OpsMineFlow WebUI. It was left running.\n' "$WEB_PORT" >&2
    UNRECOGNIZED=1
  fi
fi

if [[ -z "$API_PIDS" && -z "$WEB_PIDS" ]]; then
  if [[ "$UNRECOGNIZED" == "1" ]]; then
    exit 1
  fi
  info "OpsMineFlow is already stopped"
  exit 0
fi

if [[ -n "$WEB_PIDS" ]]; then
  info "Stopping OpsMineFlow WebUI on ${WEB_HOST}:${WEB_PORT}"
  kill $WEB_PIDS >/dev/null 2>&1 || true
fi

if [[ -n "$API_PIDS" ]]; then
  info "Stopping OpsMineFlow local API on ${API_HOST}:${API_PORT}"
  kill $API_PIDS >/dev/null 2>&1 || true
fi

wait_for_closed_port "$WEB_HOST" "$WEB_PORT" || fail "WebUI did not stop. Run lsof -nP -iTCP:${WEB_PORT} -sTCP:LISTEN"
wait_for_closed_port "$API_HOST" "$API_PORT" || fail "Local API did not stop. Run lsof -nP -iTCP:${API_PORT} -sTCP:LISTEN"

info "OpsMineFlow stopped"
[[ "$UNRECOGNIZED" == "0" ]] || exit 1
