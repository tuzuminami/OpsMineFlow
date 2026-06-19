#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PYTHON_BIN="${PYTHON_BIN:-python3}"
if [[ -x "$ROOT_DIR/.venv/bin/python" ]]; then
  PYTHON_BIN="$ROOT_DIR/.venv/bin/python"
fi

export PYTHONPATH="$ROOT_DIR/services/mining-core/src:$ROOT_DIR/services/local-api/src:$ROOT_DIR/packages/drawio-exporter/src:${PYTHONPATH:-}"

DATA_DIR="$(mktemp -d)"
API_PORT="$("$PYTHON_BIN" - <<'PY'
import socket

with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
    sock.bind(("127.0.0.1", 0))
    print(sock.getsockname()[1])
PY
)"

export OPSMINEFLOW_DATA_DIR="$DATA_DIR"
export OPSMINEFLOW_API_HOST="127.0.0.1"
export OPSMINEFLOW_API_PORT="$API_PORT"

cleanup() {
  if [[ "${API_PID:-}" != "" ]]; then
    kill "$API_PID" >/dev/null 2>&1 || true
    wait "$API_PID" 2>/dev/null || true
  fi
  rm -rf "$DATA_DIR"
}
trap cleanup EXIT INT TERM

"$PYTHON_BIN" -m opsmineflow_api >"$DATA_DIR/api.log" 2>&1 &
API_PID=$!

"$PYTHON_BIN" - "$API_PORT" <<'PY'
import json
import sys
import time
import urllib.request

port = sys.argv[1]
base = f"http://127.0.0.1:{port}"


def request(path: str, payload: dict[str, object] | None = None) -> dict[str, object] | list[object]:
    if payload is None:
        with urllib.request.urlopen(base + path, timeout=2) as response:
            return json.loads(response.read().decode("utf-8"))
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        base + path,
        data=data,
        headers={"content-type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=2) as response:
        return json.loads(response.read().decode("utf-8"))


last_error = ""
for _ in range(40):
    try:
        health = request("/health")
        if isinstance(health, dict) and health.get("status") == "ok":
            break
    except Exception as exc:  # pragma: no cover - shell smoke helper
        last_error = str(exc)
        time.sleep(0.25)
else:
    raise SystemExit(f"API did not become ready: {last_error}")

diagnostics = request("/diagnostics")
assert isinstance(diagnostics, dict)
assert diagnostics["runtime_policy"]["local_only"] is True

preview = request("/import/preview", {"format": "csv", "path": "data/sample/sample_events.csv"})
assert isinstance(preview, dict)
assert preview["event_count"] == 7
assert len(preview["sample_events"]) > 0

result = request("/import/csv", {"path": "data/sample/sample_events.csv"})
assert isinstance(result, dict)
assert result["imported_events"] == 7

history = request("/import/history")
assert isinstance(history, list)
assert history and history[0]["source"] == "csv"

settings = request("/settings", {"retention_days": 21})
assert isinstance(settings, dict)
assert settings["retention_days"] == 21

drawio = request("/export/drawio", {})
assert isinstance(drawio, dict)
assert "<mxfile" in drawio["drawio"]

deleted = request("/data/delete", {})
assert isinstance(deleted, dict)
assert deleted["deleted"] is True

events = request("/events")
assert events == []
PY

if [[ -d apps/desktop/node_modules ]]; then
  npm --prefix apps/desktop run build
fi

echo "Local smoke checks passed."
