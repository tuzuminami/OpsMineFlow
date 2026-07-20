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
export OPSMINEFLOW_INSECURE_BROWSER_DEV_API=1

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

"$PYTHON_BIN" - "$API_PORT" "$DATA_DIR" <<'PY'
import json
import os
import sys
import time
import urllib.request

port = sys.argv[1]
base_dir = sys.argv[2]
base = f"http://127.0.0.1:{port}"


def request(
    path: str,
    payload: dict[str, object] | None = None,
    headers: dict[str, str] | None = None,
) -> dict[str, object] | list[object]:
    if payload is None:
        with urllib.request.urlopen(base + path, timeout=2) as response:
            return json.loads(response.read().decode("utf-8"))
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        base + path,
        data=data,
        headers={"content-type": "application/json", **(headers or {})},
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
assert diagnostics["api"]["port"] == int(os.environ["OPSMINEFLOW_API_PORT"])
assert "dependencies" in diagnostics
assert "guardrails" in diagnostics
assert diagnostics["recording"]["capture_scope"] == "frontmost_app_only"

recording = request("/recording/status")
assert isinstance(recording, dict)
assert recording["active"] is False
assert recording["paused"] is False
assert recording["pause_intervals"] == []
assert recording["capture_scope"] == "frontmost_app_only"

mapped_path = f"{base_dir}/mapped-client.csv"
with open(mapped_path, "w", encoding="utf-8") as handle:
    handle.write("案件,作業,開始,終了,担当者,利用アプリ\n")
    handle.write("S-1,契約確認,2026/06/01 09:00,2026/06/01 09:05,佐藤,Chrome\n")
mapped_payload = {
    "path": mapped_path,
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
assert isinstance(mapped_preview, dict)
assert mapped_preview["columns"] == ["案件", "作業", "開始", "終了", "担当者", "利用アプリ"]
assert mapped_preview["event_count"] == 1
mapped_result = request("/import/csv", mapped_payload)
assert isinstance(mapped_result, dict)
assert mapped_result["imported_events"] == 1

preview = request("/import/preview", {"format": "csv", "path": "data/sample/sample_events.csv"})
assert isinstance(preview, dict)
assert preview["event_count"] == 7
assert len(preview["sample_events"]) > 0

result = request("/import/csv", {"path": "data/sample/sample_events.csv"})
assert isinstance(result, dict)
assert result["imported_events"] == 7

events = request("/events")
assert len(events) == 7
quality = request("/analytics/event-quality")
assert isinstance(quality, dict)
assert quality["summary"]["total_events"] == 7
updated = request("/events/activity", {"event_id": events[0]["event_id"], "activity": "Smoke review"})
quality_review = request("/events/quality-review", {"event_id": events[0]["event_id"], "status": "approved"})
assert quality_review["quality_review_status"] == "approved"
split = request(
    "/events/split",
    {
        "event_id": updated["event"]["event_id"],
        "split_after_seconds": 60,
        "first_activity": "Smoke review start",
        "second_activity": "Smoke review finish",
    },
)
merged = request(
    "/events/merge",
    {
        "first_event_id": split["events"][0]["event_id"],
        "second_event_id": split["events"][1]["event_id"],
        "activity": "Smoke review merged",
    },
)
excluded = request("/events/exclude", {"event_id": merged["event"]["event_id"]})
assert excluded["excluded"] is True
assert len(request("/events")) == 6

history = request("/import/history")
assert isinstance(history, list)
assert history and history[0]["source"] == "csv"

settings = request("/settings", {"retention_days": 21})
assert isinstance(settings, dict)
assert settings["retention_days"] == 21

review = request("/automation/review", {"activity": "社内確認", "status": "adopted"})
assert isinstance(review, dict)
assert review["review_status"] == "adopted"

candidates_payload = request("/analytics/automation-candidates")
assert isinstance(candidates_payload, dict)
assert isinstance(candidates_payload["candidates"], list)
assert isinstance(candidates_payload["analysis_receipt"], dict)
reviewed = next(item for item in candidates_payload["candidates"] if item["activity"] == "社内確認")
assert reviewed["review_status"] == "adopted"

drawio = request("/export/drawio", {})
assert isinstance(drawio, dict)
assert "<mxfile" in drawio["drawio"]

export_preview = request("/export/preview", {"format": "markdown"})
assert isinstance(export_preview, dict)
assert export_preview["byte_size"] > 0

saved_export = request("/export/save", {"format": "drawio", "path": f"{base_dir}/smoke-map"})
assert isinstance(saved_export, dict)
assert saved_export["saved"] is True
assert saved_export["filename"] == "smoke-map.drawio"
assert os.path.exists(os.path.join(base_dir, "smoke-map.drawio"))

challenge = request("/data/delete/challenge", {})
assert isinstance(challenge, dict)
deleted = request("/data/delete", {}, {"X-OpsMineFlow-Delete-Challenge": str(challenge["challenge"])})
assert isinstance(deleted, dict)
assert deleted["deleted"] is True

events = request("/events")
assert events == []
PY

if [[ -d apps/desktop/node_modules ]]; then
  npm --prefix apps/desktop run build
fi

echo "Local smoke checks passed."
