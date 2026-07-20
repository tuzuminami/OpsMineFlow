#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="$ROOT_DIR/.venv/bin/python"

if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="python3"
fi

PERF_DIR="$(mktemp -d)"
API_PORT="$("$PYTHON_BIN" - <<'PY'
import socket

with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
    sock.bind(("127.0.0.1", 0))
    print(sock.getsockname()[1])
PY
)"

cleanup() {
  if [[ "${API_PID:-}" != "" ]]; then
    kill "$API_PID" >/dev/null 2>&1 || true
    wait "$API_PID" 2>/dev/null || true
  fi
  rm -rf "$PERF_DIR"
}
trap cleanup EXIT INT TERM

export PYTHONPATH="$ROOT_DIR/services/local-api/src:$ROOT_DIR/services/mining-core/src:$ROOT_DIR/packages/drawio-exporter/src"
export OPSMINEFLOW_DATA_DIR="$PERF_DIR/data"
export OPSMINEFLOW_API_HOST="127.0.0.1"
export OPSMINEFLOW_API_PORT="$API_PORT"
export OPSMINEFLOW_INSECURE_BROWSER_DEV_API=1

"$PYTHON_BIN" -m opsmineflow_api >"$PERF_DIR/local-api.log" 2>&1 &
API_PID=$!

"$PYTHON_BIN" - "$API_PORT" "$PERF_DIR" <<'PY'
from __future__ import annotations

import csv
import json
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

api_port = int(sys.argv[1])
root = Path(sys.argv[2])
base_url = f"http://127.0.0.1:{api_port}"
project_headers: dict[str, str] = {}
limits = {
    "import": 15.0,
    "dashboard": 15.0,
    "page": 2.0,
    "export": 20.0,
}


def request(path: str, payload: dict[str, object] | None = None) -> tuple[object, float]:
    started = time.perf_counter()
    if payload is None:
        request_object = urllib.request.Request(base_url + path, headers=project_headers, method="GET")
    else:
        request_object = urllib.request.Request(
            base_url + path,
            data=json.dumps(payload).encode("utf-8"),
            headers={"content-type": "application/json", **project_headers},
            method="POST",
        )
    with urllib.request.urlopen(request_object, timeout=limits["export"] + 5) as response:
        decoded = json.loads(response.read().decode("utf-8"))
    return decoded, time.perf_counter() - started


def require_within(name: str, elapsed: float, limit: float) -> None:
    if elapsed > limit:
        raise SystemExit(f"{name} exceeded {limit} seconds: {elapsed:.3f}")


def wait_for_health() -> None:
    last_error = ""
    for _ in range(80):
        try:
            health, _ = request("/health")
            if isinstance(health, dict) and health.get("status") == "ok":
                return
        except Exception as error:  # pragma: no cover - bounded startup helper
            last_error = str(error)
            time.sleep(0.1)
    raise SystemExit(f"local API did not become ready: {last_error}")


def write_sources(count: int) -> tuple[Path, Path]:
    csv_path = root / f"events-{count}.csv"
    json_path = root / f"events-{count}.json"
    rows: list[dict[str, str]] = []
    with csv_path.open("w", encoding="utf-8", newline="") as csv_file:
        writer = csv.DictWriter(
            csv_file,
            fieldnames=["case_id", "activity", "timestamp_start", "timestamp_end", "user", "app_name"],
        )
        writer.writeheader()
        for index in range(count):
            started_at = datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=index * 60)
            row = {
                "case_id": f"CASE-{index // 10:05d}",
                "activity": f"Activity {index % 20}",
                "timestamp_start": started_at.isoformat(),
                "timestamp_end": (started_at + timedelta(seconds=30)).isoformat(),
                "user": "performance-user",
                "app_name": f"App {index % 8}",
            }
            writer.writerow(row)
            rows.append(row)
    json_path.write_text(json.dumps(rows), encoding="utf-8")
    return csv_path, json_path


wait_for_health()
projects, _ = request("/projects")
if not isinstance(projects, dict) or not isinstance(projects.get("active_project_id"), str):
    raise SystemExit("local API did not return an active project")
project_headers["X-OpsMineFlow-Project"] = projects["active_project_id"]
results: list[dict[str, object]] = []
dashboard_requests: tuple[tuple[str, dict[str, object] | None], ...] = (
    ("/health", None),
    ("/diagnostics", None),
    ("/recording/status", None),
    ("/settings", None),
    ("/import/history", None),
    ("/analytics/event-quality", None),
    ("/analytics/summary", None),
    ("/analytics/process-map", None),
    ("/analytics/automation-candidates", None),
    ("/analytics/app-switching", None),
    ("/reports/markdown", None),
    ("/events/page", {"offset": 0, "limit": 500}),
)

for event_count in (1_000, 10_000, 100_000):
    csv_path, json_path = write_sources(event_count)
    imported_csv, csv_import_seconds = request("/import/csv", {"path": str(csv_path)})
    if not isinstance(imported_csv, dict) or imported_csv.get("imported_events") != event_count:
        raise SystemExit(f"{event_count} events: CSV import returned an unexpected result")
    require_within(f"{event_count} events CSV import", csv_import_seconds, limits["import"])

    imported_json, json_import_seconds = request("/import/json", {"path": str(json_path)})
    if not isinstance(imported_json, dict) or imported_json.get("imported_events") != event_count:
        raise SystemExit(f"{event_count} events: JSON import returned an unexpected result")
    require_within(f"{event_count} events JSON import", json_import_seconds, limits["import"])

    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=len(dashboard_requests)) as executor:
        dashboard_results = list(executor.map(lambda item: request(*item)[0], dashboard_requests))
    dashboard_seconds = time.perf_counter() - started
    require_within(f"{event_count} events dashboard API", dashboard_seconds, limits["dashboard"])
    event_page = dashboard_results[-1]
    if not isinstance(event_page, dict) or len(event_page.get("events", [])) != 500:
        raise SystemExit(f"{event_count} events: dashboard page did not remain bounded")

    _, page_seconds = request("/events/page", {"offset": 0, "limit": 500})
    require_within(f"{event_count} events event page", page_seconds, limits["page"])

    export_measurements: dict[str, object] = {}
    for format_name in ("csv", "json"):
        staged_path = root / f"staged-{event_count}.{format_name}"
        saved, export_seconds = request(
            "/export/save",
            {"format": format_name, "path": str(staged_path), "overwrite_confirmed": False},
        )
        if not isinstance(saved, dict) or saved.get("saved") is not True or not staged_path.is_file():
            raise SystemExit(f"{event_count} events: {format_name} staging export did not complete")
        require_within(f"{event_count} events {format_name} staging export", export_seconds, limits["export"])
        export_measurements[f"{format_name}_staging_export_seconds"] = round(export_seconds, 3)
        export_measurements[f"{format_name}_bytes"] = staged_path.stat().st_size

    results.append(
        {
            "event_count": event_count,
            "csv_import_seconds": round(csv_import_seconds, 3),
            "json_import_seconds": round(json_import_seconds, 3),
            "dashboard_api_seconds": round(dashboard_seconds, 3),
            "event_page_seconds": round(page_seconds, 3),
            **export_measurements,
        }
    )

print(json.dumps({"http_performance_smoke": results, "time_limits_seconds": limits}, ensure_ascii=False))
PY
