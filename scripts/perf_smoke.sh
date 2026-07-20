#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="$ROOT_DIR/.venv/bin/python"

if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="python3"
fi

PYTHONPATH="$ROOT_DIR/services/local-api/src:$ROOT_DIR/services/mining-core/src:$ROOT_DIR/packages/drawio-exporter/src" \
  "$PYTHON_BIN" - <<'PY'
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone

from opsmineflow_api.app import create_event_page, create_export_artifact, create_process_map, create_summary
from opsmineflow_api.storage import EventStore
from opsmineflow_mining import StandardEvent


def event_at(index: int) -> StandardEvent:
    started_at = datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=index * 60)
    ended_at = started_at + timedelta(seconds=30)
    activity = f"Activity {index % 20}"
    return StandardEvent(
        event_id=f"perf-{index}",
        source="performance_smoke",
        source_event_id=str(index),
        case_id=f"CASE-{index // 10:05d}",
        session_id=f"session-{index // 10:05d}",
        user_alias="performance-user",
        user_hash="user_performance",
        device_id="local-mac",
        app_name=f"App {index % 8}",
        app_bundle_id="com.opsmineflow.performance",
        window_title="Masked test window",
        window_title_masked="Masked test window",
        url="",
        url_masked="",
        domain="",
        activity_raw=activity,
        activity_normalized=activity.casefold(),
        event_type="work_activity",
        timestamp_start=started_at.isoformat(),
        timestamp_end=ended_at.isoformat(),
        duration_seconds=30.0,
        idle_flag=False,
        confidential_flag=False,
        metadata_json="{}",
        created_at=started_at.isoformat(),
    )


events = [event_at(index) for index in range(100_000)]
TIME_LIMITS_SECONDS = {
    "page": 1.0,
    "summary": 5.0,
    "process_map": 5.0,
    "export": 15.0,
}
results: list[dict[str, object]] = []
for count in (1_000, 10_000, 100_000):
    store = EventStore(events=events[:count])
    started = time.perf_counter()
    page = create_event_page(offset=0, limit=500, store=store)
    page_seconds = time.perf_counter() - started
    if page_seconds > TIME_LIMITS_SECONDS["page"]:
        raise SystemExit(f"{count} events: page generation exceeded {TIME_LIMITS_SECONDS['page']} seconds")
    page_bytes = len(json.dumps(page, ensure_ascii=False).encode("utf-8"))
    if page_bytes > 3_100_000:
        raise SystemExit(f"{count} events: page response exceeded the IPC budget ({page_bytes} bytes)")

    started = time.perf_counter()
    summary = create_summary(store)
    summary_seconds = time.perf_counter() - started
    if summary_seconds > TIME_LIMITS_SECONDS["summary"]:
        raise SystemExit(f"{count} events: summary generation exceeded {TIME_LIMITS_SECONDS['summary']} seconds")
    if int(summary["total_events"]) != count:
        raise SystemExit(f"{count} events: summary count mismatch")

    started = time.perf_counter()
    process_map = create_process_map(store)
    process_seconds = time.perf_counter() - started
    if process_seconds > TIME_LIMITS_SECONDS["process_map"]:
        raise SystemExit(f"{count} events: process-map generation exceeded {TIME_LIMITS_SECONDS['process_map']} seconds")
    if len(process_map["nodes"]) > 500 or len(process_map["edges"]) > 1_000:
        raise SystemExit(f"{count} events: process map exceeded display bounds")

    started = time.perf_counter()
    export = create_export_artifact("markdown", store=store)
    export_seconds = time.perf_counter() - started
    if export_seconds > TIME_LIMITS_SECONDS["export"]:
        raise SystemExit(f"{count} events: Markdown export exceeded {TIME_LIMITS_SECONDS['export']} seconds")
    if int(export["byte_size"]) <= 0:
        raise SystemExit(f"{count} events: export is empty")

    results.append(
        {
            "event_count": count,
            "page_seconds": round(page_seconds, 3),
            "summary_seconds": round(summary_seconds, 3),
            "process_map_seconds": round(process_seconds, 3),
            "export_seconds": round(export_seconds, 3),
            "page_bytes": page_bytes,
            "displayed_events": len(page["events"]),
        }
    )

print(json.dumps({"performance_smoke": results, "time_limits_seconds": TIME_LIMITS_SECONDS}, ensure_ascii=False))
PY

./scripts/perf_http_smoke.sh

if [[ -d "$ROOT_DIR/apps/desktop/node_modules" ]]; then
  OPSMINEFLOW_PERF_TOTAL_EVENTS=100000 npm --prefix apps/desktop run perf:render
fi
