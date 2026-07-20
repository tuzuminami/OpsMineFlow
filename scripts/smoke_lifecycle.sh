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
export OPSMINEFLOW_INSECURE_BROWSER_DEV_API=1

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

"$ROOT_DIR/.venv/bin/python" - "$API_PORT" <<'PY'
import json
import sys
import tempfile
import urllib.request
from pathlib import Path

base = f"http://127.0.0.1:{sys.argv[1]}"
project_headers = {}

def request(path, payload=None, headers=None):
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        base + path,
        data=data,
        headers={"Content-Type": "application/json", **project_headers, **(headers or {})},
        method="GET" if payload is None else "POST",
    )
    with urllib.request.urlopen(req, timeout=2) as response:
        return json.loads(response.read().decode("utf-8"))

projects = request("/projects")
project_headers["X-OpsMineFlow-Project"] = projects["active_project_id"]

diagnostics = request("/diagnostics")
assert diagnostics["runtime_policy"]["local_only"] is True
assert diagnostics["privacy_evidence"]["status"] == "passed"
assert all(item["status"] == "not_collected" for item in diagnostics["privacy_evidence"]["items"])
assert diagnostics["recording"]["capture_scope"] == "frontmost_app_only"
assert "token_ttl_seconds" in diagnostics["recording"]
assert diagnostics["recording"]["paused"] is False
assert diagnostics["recording"]["pause_intervals"] == []

with tempfile.TemporaryDirectory() as temp_dir:
    mapped_path = Path(temp_dir) / "mapped-client.csv"
    mapped_path.write_text(
        "案件,作業,開始,終了,担当者,利用アプリ\n"
        "L-1,契約確認,2026/06/01 09:00,2026/06/01 09:05,佐藤,Chrome\n",
        encoding="utf-8",
    )
    mapped_payload = {
        "path": str(mapped_path),
        "mapping": {
            "case_id": "案件",
            "activity": "作業",
            "timestamp_start": "開始",
            "timestamp_end": "終了",
            "user": "担当者",
            "app_name": "利用アプリ",
        },
        "date_format": "%Y/%m/%d %H:%M",
        "timezone": "Asia/Tokyo",
    }
    mapped_preview = request("/import/preview", {"format": "csv", **mapped_payload})
    assert mapped_preview["columns"] == ["案件", "作業", "開始", "終了", "担当者", "利用アプリ"]
    assert mapped_preview["event_count"] == 1
    mapped_result = request("/import/csv", mapped_payload)
    assert mapped_result["imported_events"] == 1

preview = request("/import/preview", {"format": "csv", "path": "data/sample/sample_events.csv"})
assert preview["event_count"] == 7

result = request("/import/csv", {"path": "data/sample/sample_events.csv"})
assert result["imported_events"] == 7

events_payload = request("/events")
events = events_payload["events"]
assert len(events) == 7
quality = request("/analytics/event-quality")
assert quality["summary"]["total_events"] == 7
updated = request("/events/activity", {"event_id": events[0]["event_id"], "activity": "Lifecycle review"})
quality_review = request("/events/quality-review", {"event_id": events[0]["event_id"], "status": "approved"})
assert quality_review["quality_review_status"] == "approved"
split = request(
    "/events/split",
    {
        "event_id": updated["event"]["event_id"],
        "split_after_seconds": 60,
        "first_activity": "Lifecycle review start",
        "second_activity": "Lifecycle review finish",
    },
)
merged = request(
    "/events/merge",
    {
        "first_event_id": split["events"][0]["event_id"],
        "second_event_id": split["events"][1]["event_id"],
        "activity": "Lifecycle review merged",
    },
)
excluded = request("/events/exclude", {"event_id": merged["event"]["event_id"]})
assert excluded["excluded"] is True
assert len(request("/events")["events"]) == 6

export_preview = request("/export/preview", {"format": "markdown"})
assert export_preview["byte_size"] > 0
assert "Review masked fields" in export_preview["warning"]

delete_challenge = request("/data/delete/challenge", {})
delete_result = request("/data/delete", {}, {"X-OpsMineFlow-Delete-Challenge": delete_challenge["challenge"]})
assert delete_result["deleted"] is True

assert request("/events")["events"] == []
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
