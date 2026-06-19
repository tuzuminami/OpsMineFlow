from __future__ import annotations

import csv
from io import StringIO
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


class AutomationReviewRequest(BaseModel):  # type: ignore[misc, valid-type]
    activity: str
    status: str


class ExportPreviewRequest(BaseModel):  # type: ignore[misc, valid-type]
    format: str


class ExportSaveRequest(BaseModel):  # type: ignore[misc, valid-type]
    format: str
    path: str


def create_api_snapshot(store: EventStore | None = None) -> dict[str, Any]:
    active_store = store or default_store()
    events = active_store.events
    settings = active_store.get_settings()
    metrics = calculate_duration_metrics(events)
    process_map = build_directly_follows_graph(events)
    store_diagnostics = active_store.diagnostics()
    automation_candidates = apply_automation_reviews(score_automation_candidates(events), active_store)
    return {
        "health": {
            "status": "ok",
            "bind": "127.0.0.1",
            "local_only": True,
            "llm_supported": False,
            "storage_mode": store_diagnostics["storage_mode"],
            "event_count": store_diagnostics["event_count"],
        },
        "events": [event_to_api_dict(event, settings) for event in events],
        "summary": metrics_to_dict(metrics),
        "app_switching": detect_app_switches(events),
        "automation_candidates": automation_candidates,
        "process_map": process_map,
        "variants": analyze_variants(events),
        "bottlenecks": detect_bottlenecks(events),
        "markdown_report": append_automation_review_section(export_markdown_report(events), automation_candidates),
        "mermaid": export_mermaid(events),
        "drawio": build_drawio_xml(process_map),
    }


def apply_automation_reviews(candidates: list[dict[str, object]], store: EventStore) -> list[dict[str, object]]:
    reviewed: list[dict[str, object]] = []
    for candidate in candidates:
        item = dict(candidate)
        activity = str(item.get("activity", ""))
        item["review_status"] = store.automation_reviews.get(activity, "unreviewed")
        reviewed.append(item)
    return reviewed


def append_automation_review_section(markdown: str, candidates: list[dict[str, object]]) -> str:
    lines = [markdown.rstrip(), "", "## Automation Review Status"]
    if not candidates:
        lines.append("- No automation candidates found.")
    for item in candidates[:10]:
        lines.append(
            f'- {item["activity"]}: review {item["review_status"]}, '
            f'score {float(item["automation_score"]):.2f}, frequency {item["frequency"]}'
        )
    return "\n".join(lines) + "\n"


def event_to_api_dict(event: Any, settings: dict[str, object]) -> dict[str, object]:
    payload = event.to_dict()
    if not settings.get("mask_window_titles", True):
        payload["window_title_masked"] = payload["window_title"]
    if not settings.get("mask_url_paths", True):
        payload["url_masked"] = payload["url"]
    return payload


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


def create_export_artifact(format_name: str, store: EventStore | None = None) -> dict[str, Any]:
    active_store = store or default_store()
    snapshot = create_api_snapshot(active_store)
    if format_name == "markdown":
        content = str(snapshot["markdown_report"])
        extension = "md"
    elif format_name == "json":
        content = json_dumps({"snapshot": snapshot})
        extension = "json"
    elif format_name == "csv":
        content = events_to_csv(snapshot["events"])
        extension = "csv"
    elif format_name == "mermaid":
        content = str(snapshot["mermaid"])
        extension = "mmd"
    elif format_name == "drawio":
        content = str(snapshot["drawio"])
        extension = "drawio"
    else:
        raise ValueError("Export format must be markdown, json, csv, mermaid, or drawio.")

    return {
        "format": format_name,
        "extension": extension,
        "filename": f"opsmineflow-export.{extension}",
        "content": content,
        "byte_size": len(content.encode("utf-8")),
        "preview": content[:2000],
        "confidential_count": sum(1 for event in active_store.events if event.confidential_flag),
        "warning": "Review masked fields and confidential flags before sharing this export.",
    }


def save_export_artifact(format_name: str, path_value: str, store: EventStore | None = None) -> dict[str, Any]:
    if not path_value.strip():
        raise ValueError("Export path is required.")
    artifact = create_export_artifact(format_name, store=store)
    path = Path(path_value).expanduser()
    if path.exists() and path.is_dir():
        path = path / str(artifact["filename"])
    elif not path.suffix:
        path = path.with_suffix(f".{artifact['extension']}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(artifact["content"]), encoding="utf-8")
    return {
        "saved": True,
        "format": artifact["format"],
        "path": str(path),
        "byte_size": artifact["byte_size"],
        "warning": artifact["warning"],
    }


def events_to_csv(events: list[dict[str, object]]) -> str:
    columns = [
        "event_id",
        "case_id",
        "user_hash",
        "app_name",
        "window_title_masked",
        "url_masked",
        "domain",
        "activity_raw",
        "timestamp_start",
        "timestamp_end",
        "duration_seconds",
        "confidential_flag",
    ]
    buffer = StringIO()
    writer = csv.DictWriter(buffer, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(events)
    return buffer.getvalue()


def json_dumps(payload: object) -> str:
    import json

    return json.dumps(payload, ensure_ascii=False, indent=2)


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

    @app.post("/automation/review")
    def automation_review(request: AutomationReviewRequest) -> dict[str, str]:
        try:
            return default_store().set_automation_review(request.activity, request.status)
        except ValueError as exc:
            raise _bad_request(str(exc))

    @app.get("/analytics/process-map")
    def analytics_process_map() -> dict[str, Any]:
        return create_api_snapshot()["process_map"]

    @app.get("/reports/markdown")
    def report_markdown() -> dict[str, str]:
        return {"markdown": create_api_snapshot()["markdown_report"]}

    @app.post("/export/mermaid")
    def export_mermaid_endpoint() -> dict[str, str]:
        return {"mermaid": str(create_export_artifact("mermaid")["content"])}

    @app.post("/export/drawio")
    def export_drawio_endpoint() -> dict[str, str]:
        return {"drawio": str(create_export_artifact("drawio")["content"])}

    @app.post("/export/svg")
    def export_svg_endpoint() -> dict[str, str]:
        return {"status": "planned", "message": "SVG export will use a local renderer."}

    @app.post("/export/csv")
    def export_csv_endpoint() -> dict[str, Any]:
        return {"csv": str(create_export_artifact("csv")["content"]), "events": create_api_snapshot()["events"]}

    @app.post("/export/json")
    def export_json_endpoint() -> dict[str, Any]:
        return {"json": str(create_export_artifact("json")["content"]), "snapshot": create_api_snapshot()}

    @app.post("/export/preview")
    def export_preview_endpoint(request: ExportPreviewRequest) -> dict[str, Any]:
        try:
            artifact = create_export_artifact(request.format)
        except ValueError as exc:
            raise _bad_request(str(exc))
        return {key: artifact[key] for key in ("format", "filename", "byte_size", "preview", "confidential_count", "warning")}

    @app.post("/export/save")
    def export_save_endpoint(request: ExportSaveRequest) -> dict[str, Any]:
        try:
            return save_export_artifact(request.format, request.path)
        except ValueError as exc:
            raise _bad_request(str(exc))
