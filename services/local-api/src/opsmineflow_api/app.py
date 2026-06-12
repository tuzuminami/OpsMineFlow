from __future__ import annotations

from pathlib import Path
from typing import Any

from opsmineflow_drawio import build_drawio_xml
from opsmineflow_mining import (
    analyze_variants,
    build_directly_follows_graph,
    calculate_duration_metrics,
    detect_app_switches,
    detect_bottlenecks,
    export_markdown_report,
    export_mermaid,
    load_events_from_csv,
    load_events_from_json,
    score_automation_candidates,
)
from opsmineflow_mining.pipeline import metrics_to_dict

from .activitywatch import import_activitywatch_local
from .storage import EventStore, default_store

try:
    from fastapi import FastAPI, HTTPException
    from fastapi.middleware.cors import CORSMiddleware
    from pydantic import BaseModel
except ModuleNotFoundError:
    FastAPI = None  # type: ignore[assignment]
    HTTPException = Exception  # type: ignore[assignment]
    CORSMiddleware = None  # type: ignore[assignment]
    BaseModel = object  # type: ignore[assignment]


class PathImportRequest(BaseModel):  # type: ignore[misc, valid-type]
    path: str


class LabelRequest(BaseModel):  # type: ignore[misc, valid-type]
    event_id: str
    label: str


class ActivityWatchImportRequest(BaseModel):  # type: ignore[misc, valid-type]
    enabled: bool = False
    base_url: str = "http://127.0.0.1:5600"


def create_api_snapshot(store: EventStore | None = None) -> dict[str, Any]:
    active_store = store or default_store()
    events = active_store.events
    metrics = calculate_duration_metrics(events)
    process_map = build_directly_follows_graph(events)
    return {
        "health": {
            "status": "ok",
            "bind": "127.0.0.1",
            "local_only": True,
            "llm_supported": False,
        },
        "events": [event.to_dict() for event in events],
        "summary": metrics_to_dict(metrics),
        "app_switching": detect_app_switches(events),
        "automation_candidates": score_automation_candidates(events),
        "process_map": process_map,
        "variants": analyze_variants(events),
        "bottlenecks": detect_bottlenecks(events),
        "markdown_report": export_markdown_report(events),
        "mermaid": export_mermaid(events),
        "drawio": build_drawio_xml(process_map),
    }


if FastAPI is not None:
    app = FastAPI(title="OpsMineFlow Local API", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:5173", "http://localhost:5173", "tauri://localhost"],
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["content-type"],
    )
else:
    app = None


def _not_found(message: str) -> Exception:
    if FastAPI is not None:
        return HTTPException(status_code=404, detail=message)
    return FileNotFoundError(message)


if app is not None:

    @app.get("/health")
    def health() -> dict[str, Any]:
        return create_api_snapshot()["health"]

    @app.post("/import/csv")
    def import_csv(request: PathImportRequest) -> dict[str, Any]:
        path = Path(request.path)
        if not path.exists():
            raise _not_found("CSV file was not found")
        events = load_events_from_csv(path)
        default_store().replace(events)
        return {"imported_events": len(events), "source": "csv"}

    @app.post("/import/json")
    def import_json(request: PathImportRequest) -> dict[str, Any]:
        path = Path(request.path)
        if not path.exists():
            raise _not_found("JSON file was not found")
        events = load_events_from_json(path)
        default_store().replace(events)
        return {"imported_events": len(events), "source": "json"}

    @app.post("/import/activitywatch-local")
    def import_activitywatch(request: ActivityWatchImportRequest) -> dict[str, Any]:
        if not request.enabled:
            return {"imported_events": 0, "message": "ActivityWatch import is disabled until explicitly enabled."}
        events = import_activitywatch_local(request.base_url)
        default_store().replace(events)
        return {"imported_events": len(events), "source": "activitywatch_local"}

    @app.get("/events")
    def events() -> list[dict[str, Any]]:
        return create_api_snapshot()["events"]

    @app.post("/events/label")
    def label_event(request: LabelRequest) -> dict[str, Any]:
        default_store().manual_labels[request.event_id] = request.label
        return {"event_id": request.event_id, "label": request.label}

    @app.get("/analytics/summary")
    def analytics_summary() -> dict[str, Any]:
        return create_api_snapshot()["summary"]

    @app.get("/analytics/app-switching")
    def analytics_app_switching() -> dict[str, Any]:
        return create_api_snapshot()["app_switching"]

    @app.get("/analytics/automation-candidates")
    def analytics_automation_candidates() -> list[dict[str, Any]]:
        return create_api_snapshot()["automation_candidates"]

    @app.get("/analytics/process-map")
    def analytics_process_map() -> dict[str, Any]:
        return create_api_snapshot()["process_map"]

    @app.get("/reports/markdown")
    def report_markdown() -> dict[str, str]:
        return {"markdown": create_api_snapshot()["markdown_report"]}

    @app.post("/export/mermaid")
    def export_mermaid_endpoint() -> dict[str, str]:
        return {"mermaid": create_api_snapshot()["mermaid"]}

    @app.post("/export/drawio")
    def export_drawio_endpoint() -> dict[str, str]:
        return {"drawio": create_api_snapshot()["drawio"]}

    @app.post("/export/svg")
    def export_svg_endpoint() -> dict[str, str]:
        return {"status": "planned", "message": "SVG export will use a local renderer."}

    @app.post("/export/csv")
    def export_csv_endpoint() -> dict[str, list[dict[str, Any]]]:
        return {"events": create_api_snapshot()["events"]}

    @app.post("/export/json")
    def export_json_endpoint() -> dict[str, Any]:
        return {"snapshot": create_api_snapshot()}

