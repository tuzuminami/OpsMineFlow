#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ ! -x "$ROOT_DIR/.venv/bin/python" || ! -d "$ROOT_DIR/apps/desktop/node_modules" ]]; then
  echo "Lifecycle smoke skipped because installed dependencies are unavailable."
  exit 0
fi

read -r API_PORT WEB_PORT < <("$ROOT_DIR/.venv/bin/python" - <<'PY'
import socket

sockets = []
ports = []
for _ in range(2):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    sockets.append(sock)
    ports.append(sock.getsockname()[1])
print(*ports)
for sock in sockets:
    sock.close()
PY
)

SMOKE_DIR="$(mktemp -d)"
export OPSMINEFLOW_API_PORT="$API_PORT"
export OPSMINEFLOW_WEB_PORT="$WEB_PORT"
export OPSMINEFLOW_DATA_DIR="$SMOKE_DIR/data"
export OPSMINEFLOW_NO_OPEN=1

cleanup() {
  ./scripts/stop_local.sh >/dev/null 2>&1 || true
  if [[ "${RUN_PID:-}" != "" ]]; then
    kill "$RUN_PID" >/dev/null 2>&1 || true
    wait "$RUN_PID" 2>/dev/null || true
  fi
  rm -rf "$SMOKE_DIR"
}
trap cleanup EXIT INT TERM

./scripts/run_local.sh >"$SMOKE_DIR/first-run.log" 2>&1 &
RUN_PID=$!

"$ROOT_DIR/.venv/bin/python" - "$API_PORT" "$WEB_PORT" <<'PY'
import json
import sys
import time
import urllib.request

api_url = f"http://127.0.0.1:{sys.argv[1]}/health"
web_url = f"http://127.0.0.1:{sys.argv[2]}"
last_error = ""
for _ in range(80):
    try:
        api_request = urllib.request.Request(api_url, headers={"Origin": web_url})
        with urllib.request.urlopen(api_request, timeout=0.5) as response:
            health = json.loads(response.read().decode("utf-8"))
            allowed_origin = response.headers.get("access-control-allow-origin")
        with urllib.request.urlopen(web_url, timeout=0.5) as response:
            html = response.read().decode("utf-8")
        if (
            health.get("status") == "ok"
            and health.get("local_only") is True
            and allowed_origin == web_url
            and "<title>OpsMineFlow</title>" in html
        ):
            raise SystemExit(0)
    except Exception as exc:
        last_error = str(exc)
    time.sleep(0.25)
raise SystemExit(f"Lifecycle services did not become ready: {last_error}")
PY

SECOND_OUTPUT="$(./scripts/run_local.sh)"
[[ "$SECOND_OUTPUT" == *"OpsMineFlow is ready"* ]]

./scripts/stop_local.sh
wait "$RUN_PID" 2>/dev/null || true
RUN_PID=""

"$ROOT_DIR/.venv/bin/python" - "$API_PORT" "$WEB_PORT" <<'PY'
import socket
import sys

for raw_port in sys.argv[1:]:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.2)
        if sock.connect_ex(("127.0.0.1", int(raw_port))) == 0:
            raise SystemExit(f"Port {raw_port} remained open after stop")
PY

echo "Lifecycle smoke checks passed."
