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


class ImportPreviewRequest(BaseModel):  # type: ignore[misc, valid-type]
    format: str
    path: str


class LabelRequest(BaseModel):  # type: ignore[misc, valid-type]
    event_id: str
    label: str


class ActivityWatchImportRequest(BaseModel):  # type: ignore[misc, valid-type]
    enabled: bool = False
    base_url: str = "http://127.0.0.1:5600"


class SettingsRequest(BaseModel):  # type: ignore[misc, valid-type]
    mask_url_paths: bool | None = None
    mask_window_titles: bool | None = None
    retention_days: int | None = None
    activitywatch_enabled: bool | None = None
    excluded_apps: list[str] | None = None
    excluded_domains: list[str] | None = None


def create_api_snapshot(store: EventStore | None = None) -> dict[str, Any]:
    active_store = store or default_store()
    events = active_store.events
    metrics = calculate_duration_metrics(events)
    process_map = build_directly_follows_graph(events)
    store_diagnostics = active_store.diagnostics()
    return {
        "health": {
            "status": "ok",
            "bind": "127.0.0.1",
            "local_only": True,
            "llm_supported": False,
            "storage_mode": store_diagnostics["storage_mode"],
            "event_count": store_diagnostics["event_count"],
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


def load_events_for_import(format_name: str, path: Path) -> list[Any]:
    if not path.exists():
        raise FileNotFoundError(f"{format_name.upper()} file was not found")
    if format_name == "csv":
        return load_events_from_csv(path)
    if format_name == "json":
        return load_events_from_json(path)
    raise ValueError("Import format must be csv or json.")


def create_import_preview(format_name: str, path_value: str) -> dict[str, Any]:
    path = Path(path_value)
    events = load_events_for_import(format_name, path)
    return {
        "format": format_name,
        "path": str(path),
        "event_count": len(events),
        "confidential_count": sum(1 for event in events if event.confidential_flag),
        "sample_events": [
            {
                "case_id": event.case_id,
                "activity": event.activity_raw,
                "app_name": event.app_name,
                "domain": event.domain,
                "duration_seconds": event.duration_seconds,
            }
            for event in events[:5]
        ],
    }


def import_path_into_store(format_name: str, path_value: str, store: EventStore | None = None) -> dict[str, Any]:
    path = Path(path_value)
    events = load_events_for_import(format_name, path)
    active_store = store or default_store()
    active_store.replace(events, import_source=format_name, import_path=str(path))
    return {"imported_events": len(events), "source": format_name}


def create_diagnostics(store: EventStore | None = None) -> dict[str, Any]:
    active_store = store or default_store()
    diagnostics = active_store.diagnostics()
    return {
        "api": {
            "status": "ok",
            "bind": "127.0.0.1",
            "cors": ["http://127.0.0.1:5173", "http://localhost:5173", "tauri://localhost"],
        },
        "storage": diagnostics,
        "runtime_policy": {
            "local_only": True,
            "external_network": "blocked_by_policy",
            "llm_supported": False,
            "remote_reporting": False,
        },
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


def _bad_request(message: str) -> Exception:
    if FastAPI is not None:
        return HTTPException(status_code=400, detail=message)
    return ValueError(message)


if app is not None:

    @app.get("/health")
    def health() -> dict[str, Any]:
        return create_api_snapshot()["health"]

    @app.get("/diagnostics")
    def diagnostics() -> dict[str, Any]:
        return create_diagnostics()

    @app.get("/settings")
    def settings() -> dict[str, object]:
        return default_store().get_settings()

    @app.get("/import/history")
    def import_history() -> list[dict[str, object]]:
        return default_store().list_import_history()

    @app.post("/import/preview")
    def import_preview(request: ImportPreviewRequest) -> dict[str, Any]:
        try:
            return create_import_preview(request.format, request.path)
        except FileNotFoundError as exc:
            raise _not_found(str(exc))
        except ValueError as exc:
            raise _bad_request(str(exc))

    @app.post("/settings")
    def update_settings(request: SettingsRequest) -> dict[str, object]:
        return default_store().update_settings(request.model_dump(exclude_none=True))

    @app.post("/import/csv")
    def import_csv(request: PathImportRequest) -> dict[str, Any]:
        try:
            return import_path_into_store("csv", request.path)
        except FileNotFoundError as exc:
            raise _not_found(str(exc))

    @app.post("/import/json")
    def import_json(request: PathImportRequest) -> dict[str, Any]:
        try:
            return import_path_into_store("json", request.path)
        except FileNotFoundError as exc:
            raise _not_found(str(exc))

    @app.post("/import/activitywatch-local")
    def import_activitywatch(request: ActivityWatchImportRequest) -> dict[str, Any]:
        if not request.enabled:
            return {"imported_events": 0, "message": "ActivityWatch import is disabled until explicitly enabled."}
        events = import_activitywatch_local(request.base_url)
        default_store().replace(events, import_source="activitywatch_local", import_path=request.base_url)
        return {"imported_events": len(events), "source": "activitywatch_local"}

    @app.get("/events")
    def events() -> list[dict[str, Any]]:
        return create_api_snapshot()["events"]

    @app.post("/events/label")
    def label_event(request: LabelRequest) -> dict[str, Any]:
        try:
            default_store().set_label(request.event_id, request.label)
        except KeyError:
            raise _not_found("Event was not found")
        return {"event_id": request.event_id, "label": request.label}

    @app.post("/data/delete")
    def delete_data() -> dict[str, Any]:
        default_store().clear()
        return {"deleted": True}

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
