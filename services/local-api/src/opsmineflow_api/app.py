from __future__ import annotations

import csv
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from typing import Any

# A launcher secret belongs to the Rust parent, not to this long-running Python
# process or any diagnostics it may invoke. Capture the ownership nonce needed
# for the loopback health check, then remove both launcher values before loading
# the rest of the application.
_RUNTIME_NONCE = os.environ.pop("OPSMINEFLOW_RUNTIME_NONCE", "").strip()
os.environ.pop("OPSMINEFLOW_RUNTIME_SECRET", None)

from opsmineflow_drawio import build_drawio_xml
from opsmineflow_mining import (
    StandardEvent,
    analyze_variants,
    build_directly_follows_graph,
    calculate_duration_metrics,
    detect_app_switches,
    detect_bottlenecks,
    export_markdown_report,
    export_mermaid,
    inspect_csv_columns,
    load_events_from_csv,
    load_events_from_csv_with_mapping,
    load_events_from_json,
    score_automation_candidates,
    suggest_csv_mapping,
)
from opsmineflow_mining.pipeline import metrics_to_dict

from .activitywatch import import_activitywatch_local
from .child_process import sanitized_subprocess_environment
from .recording import recording_manager
from .storage import EventStore, default_store

try:
    from fastapi import FastAPI, Header, HTTPException
    from fastapi.middleware.cors import CORSMiddleware
    from pydantic import BaseModel
except ModuleNotFoundError:
    FastAPI = None  # type: ignore[assignment]
    HTTPException = Exception  # type: ignore[assignment]
    CORSMiddleware = None  # type: ignore[assignment]
    Header = None  # type: ignore[assignment]
    BaseModel = object  # type: ignore[assignment]


class PathImportRequest(BaseModel):  # type: ignore[misc, valid-type]
    path: str
    mapping: dict[str, str] | None = None
    date_format: str = ""
    timezone: str = "UTC"


class ImportPreviewRequest(BaseModel):  # type: ignore[misc, valid-type]
    format: str
    path: str
    mapping: dict[str, str] | None = None
    date_format: str = ""
    timezone: str = "UTC"


class LabelRequest(BaseModel):  # type: ignore[misc, valid-type]
    event_id: str
    label: str


class EventActivityUpdateRequest(BaseModel):  # type: ignore[misc, valid-type]
    event_id: str
    activity: str


class EventExcludeRequest(BaseModel):  # type: ignore[misc, valid-type]
    event_id: str


class EventQualityReviewRequest(BaseModel):  # type: ignore[misc, valid-type]
    event_id: str
    status: str = "approved"


class EventSplitRequest(BaseModel):  # type: ignore[misc, valid-type]
    event_id: str
    split_after_seconds: float
    first_activity: str = ""
    second_activity: str = ""


class EventMergeRequest(BaseModel):  # type: ignore[misc, valid-type]
    first_event_id: str
    second_event_id: str
    activity: str = ""


class ActivityWatchImportRequest(BaseModel):  # type: ignore[misc, valid-type]
    enabled: bool = False
    base_url: str = "http://127.0.0.1:5600"
    mode: str = "replace"


class ActivityWatchPreviewRequest(BaseModel):  # type: ignore[misc, valid-type]
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
    note: str = ""


class ExportPreviewRequest(BaseModel):  # type: ignore[misc, valid-type]
    format: str


class ExportSaveRequest(BaseModel):  # type: ignore[misc, valid-type]
    format: str
    path: str


class RecordingStartRequest(BaseModel):  # type: ignore[misc, valid-type]
    case_id: str
    activity_label: str
    consent: bool = False


class RecordingPauseRequest(BaseModel):  # type: ignore[misc, valid-type]
    reason: str = ""


class RecordingEventRequest(BaseModel):  # type: ignore[misc, valid-type]
    session_id: str
    sequence: int
    app_name: str
    app_bundle_id: str = ""
    timestamp_start: str
    timestamp_end: str
    duration_seconds: float


class RecordingHeartbeatRequest(BaseModel):  # type: ignore[misc, valid-type]
    session_id: str
    current_app: str = ""


def allowed_webui_origins() -> list[str]:
    webui_port = int(os.environ.get("OPSMINEFLOW_WEBUI_PORT", "5173"))
    return [
        f"http://127.0.0.1:{webui_port}",
        f"http://localhost:{webui_port}",
        "tauri://localhost",
    ]


def create_api_snapshot(store: EventStore | None = None) -> dict[str, Any]:
    active_store = store or default_store()
    events = active_store.events
    settings = active_store.get_settings()
    metrics = calculate_duration_metrics(events)
    process_map = build_directly_follows_graph(events)
    store_diagnostics = active_store.diagnostics()
    automation_candidates = apply_automation_reviews(score_automation_candidates(events), active_store, events)
    health = {
        **create_runtime_health(),
        "storage_mode": store_diagnostics["storage_mode"],
        "event_count": store_diagnostics["event_count"],
    }
    return {
        "health": health,
        "events": [event_to_api_dict(event, settings) for event in events],
        "summary": metrics_to_dict(metrics),
        "app_switching": detect_app_switches(events),
        "automation_candidates": automation_candidates,
        "event_quality": create_event_quality_report(active_store),
        "process_map": process_map,
        "variants": analyze_variants(events),
        "bottlenecks": detect_bottlenecks(events),
        "markdown_report": append_automation_review_section(export_markdown_report(events), automation_candidates),
        "mermaid": export_mermaid(events),
        "drawio": build_drawio_xml(process_map),
    }


def create_runtime_health() -> dict[str, Any]:
    """Return a constant-time loopback ownership payload for the Rust launcher."""

    health: dict[str, Any] = {
        "status": "ok",
        "bind": "127.0.0.1",
        "local_only": True,
        "llm_supported": False,
    }
    if _RUNTIME_NONCE:
        health["runtime"] = {"nonce": _RUNTIME_NONCE, "pid": os.getpid()}
    return health


def apply_automation_reviews(
    candidates: list[dict[str, object]],
    store: EventStore,
    events: list[Any],
) -> list[dict[str, object]]:
    activity_profiles = _automation_activity_profiles(events)
    reviewed: list[dict[str, object]] = []
    for candidate in candidates:
        item = dict(candidate)
        activity = str(item.get("activity", ""))
        item.update(_automation_portfolio_fields(item, activity_profiles.get(activity, {})))
        item["review_status"] = store.automation_reviews.get(activity, "unreviewed")
        item["review_note"] = store.automation_review_notes.get(activity, "")
        reviewed.append(item)
    return reviewed


def _automation_activity_profiles(events: list[Any]) -> dict[str, dict[str, object]]:
    profiles: dict[str, dict[str, object]] = {}
    for event in events:
        activity = str(event.activity_raw)
        profile = profiles.setdefault(activity, {"total_seconds": 0.0, "durations": [], "confidential_count": 0})
        profile["total_seconds"] = float(profile["total_seconds"]) + float(event.duration_seconds)
        durations = profile["durations"]
        if isinstance(durations, list):
            durations.append(float(event.duration_seconds))
        if bool(event.confidential_flag):
            profile["confidential_count"] = int(profile["confidential_count"]) + 1
    return profiles


def _automation_portfolio_fields(candidate: dict[str, object], profile: dict[str, object]) -> dict[str, object]:
    classification = str(candidate.get("classification", "improvement_review"))
    frequency = int(candidate.get("frequency", 0) or 0)
    score = float(candidate.get("automation_score", 0.0) or 0.0)
    component_scores = candidate.get("component_scores", {})
    if not isinstance(component_scores, dict):
        component_scores = {}
    total_seconds = float(profile.get("total_seconds", 0.0) or 0.0)
    confidential_count = int(profile.get("confidential_count", 0) or 0)
    savings_ratio = _automation_savings_ratio(classification, component_scores)
    estimated_minutes = round(total_seconds * savings_ratio / 60, 1)
    impact_score = min(100, round(score * 100 + min(estimated_minutes, 30)))
    difficulty_score = _automation_difficulty_score(classification, frequency, component_scores)
    risk_score = _automation_risk_score(classification, confidential_count, component_scores)
    return {
        "impact_score": impact_score,
        "estimated_time_savings_minutes": estimated_minutes,
        "implementation_difficulty": _score_level(difficulty_score, low=45, high=70),
        "implementation_difficulty_score": difficulty_score,
        "risk_level": _score_level(risk_score, low=35, high=65),
        "risk_score": risk_score,
        "required_data": _automation_required_data(classification, component_scores),
        "recommended_action": _automation_recommended_action(classification),
        "portfolio_quadrant": _automation_quadrant(impact_score, difficulty_score),
    }


def _automation_savings_ratio(classification: str, component_scores: dict[object, object]) -> float:
    if classification == "rpa":
        return 0.45
    if classification == "system_change":
        return 0.35
    if classification == "operations_rule_change":
        return 0.25
    if float(component_scores.get("manual_transfer_risk_score", 0.0) or 0.0) >= 1.0:
        return 0.35
    return 0.2


def _automation_difficulty_score(classification: str, frequency: int, component_scores: dict[object, object]) -> int:
    base_scores = {
        "operations_rule_change": 30,
        "improvement_review": 45,
        "rpa": 60,
        "system_change": 75,
    }
    score = base_scores.get(classification, 50)
    if frequency <= 1:
        score += 10
    if float(component_scores.get("system_handover_score", 0.0) or 0.0) >= 1.0 and classification != "system_change":
        score += 8
    return min(score, 100)


def _automation_risk_score(classification: str, confidential_count: int, component_scores: dict[object, object]) -> int:
    base_scores = {
        "operations_rule_change": 25,
        "improvement_review": 35,
        "rpa": 50,
        "system_change": 65,
    }
    score = base_scores.get(classification, 40)
    if confidential_count:
        score += 20
    if float(component_scores.get("manual_transfer_risk_score", 0.0) or 0.0) >= 1.0:
        score += 10
    return min(score, 100)


def _score_level(score: int, low: int, high: int) -> str:
    if score <= low:
        return "low"
    if score >= high:
        return "high"
    return "medium"


def _automation_required_data(classification: str, component_scores: dict[object, object]) -> list[str]:
    required = ["event_samples", "volume_frequency"]
    if classification == "rpa" or float(component_scores.get("manual_transfer_risk_score", 0.0) or 0.0) >= 1.0:
        required.append("source_destination_fields")
    if classification == "system_change":
        required.extend(["system_owner", "interface_constraints"])
    if classification == "operations_rule_change":
        required.append("current_rule")
    required.append("exception_cases")
    return required


def _automation_recommended_action(classification: str) -> str:
    actions = {
        "rpa": "rpa_assessment",
        "system_change": "system_integration_review",
        "operations_rule_change": "standardize_rule",
        "improvement_review": "process_review",
    }
    return actions.get(classification, "process_review")


def _automation_quadrant(impact_score: int, difficulty_score: int) -> str:
    if impact_score >= 70 and difficulty_score <= 50:
        return "quick_win"
    if impact_score >= 70:
        return "strategic"
    if difficulty_score <= 50:
        return "low_effort"
    return "evaluate_later"


def append_automation_review_section(markdown: str, candidates: list[dict[str, object]]) -> str:
    lines = [markdown.rstrip(), "", "## Automation Priority Portfolio"]
    if not candidates:
        lines.append("- No automation candidates found.")
    else:
        lines.append("| Activity | Review | Impact | Difficulty | Risk | Est. saved min | Recommended action | Note |")
        lines.append("|---|---|---:|---|---|---:|---|---|")
    for item in candidates[:10]:
        lines.append(
            f'| {item["activity"]} | {item["review_status"]} | {item["impact_score"]} | '
            f'{item["implementation_difficulty"]} | {item["risk_level"]} | '
            f'{item["estimated_time_savings_minutes"]} | {item["recommended_action"]} | '
            f'{str(item.get("review_note", "")).replace("|", "/")} |'
        )
    lines.extend(["", "## Automation Review Status"])
    for item in candidates[:10]:
        lines.append(
            f'- {item["activity"]}: review {item["review_status"]}, '
            f'score {float(item["automation_score"]):.2f}, frequency {item["frequency"]}, '
            f'impact {item["impact_score"]}, difficulty {item["implementation_difficulty"]}, risk {item["risk_level"]}'
        )
    return "\n".join(lines) + "\n"


def create_event_quality_report(store: EventStore | None = None) -> dict[str, Any]:
    active_store = store or default_store()
    items: list[dict[str, Any]] = []
    issue_totals = {
        "missing_fields": 0,
        "invalid_time": 0,
        "zero_duration": 0,
        "short_duration": 0,
        "long_duration": 0,
        "unlabeled": 0,
        "low_confidence": 0,
    }
    approved_count = 0
    for event in active_store.events:
        status = _event_quality_status(event)
        if status == "approved":
            approved_count += 1
        issues = _event_quality_issues(event)
        for issue in issues:
            code = str(issue["code"])
            if code in issue_totals:
                issue_totals[code] += 1
        if issues:
            items.append(
                {
                    "event_id": event.event_id,
                    "case_id": event.case_id,
                    "activity": event.activity_raw,
                    "app_name": event.app_name,
                    "timestamp_start": event.timestamp_start,
                    "timestamp_end": event.timestamp_end,
                    "duration_seconds": event.duration_seconds,
                    "quality_review_status": status,
                    "issues": issues,
                    "recommended_action": _quality_recommended_action(issues),
                }
            )
    unresolved_items = [item for item in items if item["quality_review_status"] != "approved"]
    return {
        "summary": {
            "total_events": len(active_store.events),
            "affected_event_count": len(unresolved_items),
            "issue_count": sum(len(item["issues"]) for item in unresolved_items),
            "approved_count": approved_count,
            **issue_totals,
        },
        "items": items,
    }


def _event_quality_issues(event: Any) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    missing = []
    if not event.case_id.strip():
        missing.append("case_id")
    if not event.activity_raw.strip():
        missing.append("activity")
    if not event.timestamp_start.strip() or not event.timestamp_end.strip():
        missing.append("timestamp")
    if not event.app_name.strip():
        missing.append("app_name")
    if missing:
        issues.append(
            {
                "code": "missing_fields",
                "severity": "high",
                "label": f"Missing fields: {', '.join(missing)}",
                "remediation": "Edit the event label or exclude the interval before analysis.",
            }
        )

    try:
        start = _parse_event_time(event.timestamp_start)
        end = _parse_event_time(event.timestamp_end)
        if end < start:
            issues.append(
                {
                    "code": "invalid_time",
                    "severity": "high",
                    "label": "End time is before start time.",
                    "remediation": "Split, merge, or exclude this interval.",
                }
            )
    except ValueError:
        issues.append(
            {
                "code": "invalid_time",
                "severity": "high",
                "label": "Timestamp format could not be parsed.",
                "remediation": "Fix the source data and import again, or exclude this interval.",
            }
        )

    duration = float(event.duration_seconds)
    if duration <= 0:
        issues.append(
            {
                "code": "zero_duration",
                "severity": "high",
                "label": "Duration is zero or negative.",
                "remediation": "Exclude this interval or fix the source timestamps.",
            }
        )
    elif duration < 3:
        issues.append(
            {
                "code": "short_duration",
                "severity": "medium",
                "label": "Duration is very short.",
                "remediation": "Merge with a neighboring interval if it is noise.",
            }
        )
    elif duration >= 30 * 60:
        issues.append(
            {
                "code": "long_duration",
                "severity": "medium",
                "label": "Duration is unusually long.",
                "remediation": "Split it or exclude it as a break if it was not work.",
            }
        )

    normalized_activity = " ".join(event.activity_raw.strip().casefold().split())
    if normalized_activity in {"", "unlabeled activity", "unknown"}:
        issues.append(
            {
                "code": "unlabeled",
                "severity": "high",
                "label": "Work label is missing or generic.",
                "remediation": "Enter a business-friendly work label.",
            }
        )
    elif event.activity_raw == event.app_name or event.activity_raw.endswith(f" / {event.app_name}"):
        issues.append(
            {
                "code": "low_confidence",
                "severity": "low",
                "label": "Work label may still be app-based.",
                "remediation": "Rename it to the actual business activity if needed.",
            }
        )
    return issues


def _quality_recommended_action(issues: list[dict[str, str]]) -> str:
    codes = {issue["code"] for issue in issues}
    if "missing_fields" in codes or "unlabeled" in codes or "low_confidence" in codes:
        return "edit_label"
    if "long_duration" in codes or "zero_duration" in codes or "invalid_time" in codes:
        return "split_or_exclude"
    return "review"


def _event_quality_status(event: Any) -> str:
    try:
        metadata = json.loads(event.metadata_json) if event.metadata_json else {}
    except json.JSONDecodeError:
        return "unreviewed"
    if not isinstance(metadata, dict):
        return "unreviewed"
    return str(metadata.get("quality_review_status") or "unreviewed")


def _parse_event_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def event_to_api_dict(event: Any, settings: dict[str, object]) -> dict[str, object]:
    payload = event.to_dict()
    payload["quality_review_status"] = _event_quality_status(event)
    if not settings.get("mask_window_titles", True):
        payload["window_title_masked"] = payload["window_title"]
    if not settings.get("mask_url_paths", True):
        payload["url_masked"] = payload["url"]
    return payload


def load_events_for_import(
    format_name: str,
    path: Path,
    mapping: dict[str, str] | None = None,
    date_format: str = "",
    timezone_name: str = "UTC",
) -> list[Any]:
    if not path.exists():
        raise FileNotFoundError(f"{format_name.upper()} file was not found")
    if format_name == "csv":
        if mapping is not None and any(value.strip() for value in mapping.values()):
            return load_events_from_csv_with_mapping(path, mapping, date_format=date_format, timezone_name=timezone_name)
        return load_events_from_csv(path)
    if format_name == "json":
        return load_events_from_json(path)
    raise ValueError("Import format must be csv or json.")


def create_import_preview(
    format_name: str,
    path_value: str,
    mapping: dict[str, str] | None = None,
    date_format: str = "",
    timezone_name: str = "UTC",
) -> dict[str, Any]:
    path = Path(path_value)
    columns: list[str] = []
    sample_rows: list[dict[str, str]] = []
    suggested_mapping: dict[str, str] = {}
    mapping_warnings: list[str] = []
    effective_mapping = mapping
    if format_name == "csv":
        inspection = inspect_csv_columns(path)
        columns = [str(column) for column in inspection["columns"]]
        sample_rows = [
            {str(key): str(value) for key, value in row.items()}
            for row in inspection["sample_rows"]  # type: ignore[union-attr]
        ]
        suggested_mapping = suggest_csv_mapping(columns)
        if mapping is None or not any(value.strip() for value in mapping.values()):
            effective_mapping = suggested_mapping
    try:
        events = load_events_for_import(format_name, path, effective_mapping, date_format, timezone_name)
    except ValueError as exc:
        if format_name != "csv":
            raise
        events = []
        mapping_warnings.append(str(exc))
    return {
        "format": format_name,
        "path": str(path),
        "event_count": len(events),
        "confidential_count": sum(1 for event in events if event.confidential_flag),
        "columns": columns,
        "sample_rows": sample_rows,
        "suggested_mapping": suggested_mapping,
        "mapping": effective_mapping or {},
        "mapping_warnings": mapping_warnings,
        "date_format": date_format,
        "timezone": timezone_name,
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


def import_path_into_store(
    format_name: str,
    path_value: str,
    store: EventStore | None = None,
    mapping: dict[str, str] | None = None,
    date_format: str = "",
    timezone_name: str = "UTC",
) -> dict[str, Any]:
    path = Path(path_value)
    events = load_events_for_import(format_name, path, mapping, date_format, timezone_name)
    active_store = store or default_store()
    active_store.replace(events, import_source=format_name, import_path=str(path))
    return {"imported_events": len(events), "source": format_name}


ACTIVITYWATCH_IMPORT_MODES = {"replace", "append", "skip_duplicates"}


def create_activitywatch_preview(
    enabled: bool,
    base_url: str = "http://127.0.0.1:5600",
    store: EventStore | None = None,
) -> dict[str, Any]:
    if not enabled:
        return _activitywatch_preview_payload([], store or default_store(), False, base_url)
    return _activitywatch_preview_payload(import_activitywatch_local(base_url), store or default_store(), True, base_url)


def import_activitywatch_into_store(
    enabled: bool,
    base_url: str = "http://127.0.0.1:5600",
    mode: str = "replace",
    store: EventStore | None = None,
) -> dict[str, Any]:
    normalized_mode = _normalize_activitywatch_mode(mode)
    if not enabled:
        return {
            "imported_events": 0,
            "source": "activitywatch_local",
            "mode": normalized_mode,
            "message": "ActivityWatch import is disabled until explicitly enabled.",
        }

    active_store = store or default_store()
    events = import_activitywatch_local(base_url)
    preview = _activitywatch_preview_payload(events, active_store, True, base_url)
    existing_ids = {event.event_id for event in active_store.events}
    importable_events = active_store._filter_events(list(events))

    if normalized_mode == "replace":
        active_store.replace(events, import_source="activitywatch_local", import_path=base_url)
        imported_events = len(active_store.events)
        skipped_duplicates = 0
    else:
        imported_events = active_store.append(events)
        skipped_duplicates = sum(1 for event in importable_events if event.event_id in existing_ids)
        active_store.record_import(f"activitywatch_local_{normalized_mode}", base_url, imported_events)

    return {
        "imported_events": imported_events,
        "source": "activitywatch_local",
        "mode": normalized_mode,
        "fetched_events": preview["event_count"],
        "skipped_duplicates": skipped_duplicates,
        "excluded_events": preview["excluded_event_count"],
        "message": "",
    }


def _normalize_activitywatch_mode(mode: str) -> str:
    normalized = (mode or "replace").strip().casefold()
    if normalized not in ACTIVITYWATCH_IMPORT_MODES:
        raise ValueError("ActivityWatch import mode must be replace, append, or skip_duplicates.")
    return normalized


def _activitywatch_preview_payload(
    events: list[StandardEvent],
    store: EventStore,
    enabled: bool,
    base_url: str,
) -> dict[str, Any]:
    filtered_events = store._filter_events(list(events))
    existing_ids = {event.event_id for event in store.events}
    duplicate_count = sum(1 for event in filtered_events if event.event_id in existing_ids)
    period_start, period_end = _event_period(events)
    return {
        "enabled": enabled,
        "local_only": True,
        "base_url": base_url,
        "event_count": len(events),
        "importable_event_count": len(filtered_events),
        "duplicate_count": duplicate_count,
        "new_event_count": max(len(filtered_events) - duplicate_count, 0),
        "excluded_event_count": max(len(events) - len(filtered_events), 0),
        "confidential_count": sum(1 for event in events if event.confidential_flag),
        "period_start": period_start,
        "period_end": period_end,
        "app_usage_seconds": _app_usage_seconds(events),
        "sample_events": [
            {
                "case_id": event.case_id,
                "activity": event.activity_raw,
                "app_name": event.app_name,
                "domain": event.domain,
                "duration_seconds": event.duration_seconds,
            }
            for event in filtered_events[:5]
        ],
        "message": "" if enabled else "ActivityWatch import is disabled until explicitly enabled.",
    }


def _event_period(events: list[StandardEvent]) -> tuple[str, str]:
    starts: list[datetime] = []
    ends: list[datetime] = []
    for event in events:
        try:
            starts.append(_parse_event_time(event.timestamp_start))
            ends.append(_parse_event_time(event.timestamp_end))
        except ValueError:
            continue
    if not starts or not ends:
        return "", ""
    return min(starts).isoformat(), max(ends).isoformat()


def _app_usage_seconds(events: list[StandardEvent]) -> dict[str, float]:
    usage: dict[str, float] = {}
    for event in events:
        app_name = event.app_name or "Unknown"
        usage[app_name] = usage.get(app_name, 0.0) + float(event.duration_seconds)
    return dict(sorted(usage.items(), key=lambda item: item[1], reverse=True)[:5])


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
    settings = active_store.get_settings()
    api_port = int(os.environ.get("OPSMINEFLOW_API_PORT", "8765"))
    webui_port = int(os.environ.get("OPSMINEFLOW_WEBUI_PORT", "5173"))
    activitywatch_enabled = bool(settings.get("activitywatch_enabled", False))
    return {
        "api": {
            "status": "ok",
            "bind": "127.0.0.1",
            "port": api_port,
            "cors": allowed_webui_origins(),
        },
        "webui": {
            "status": "reachable" if _tcp_open("127.0.0.1", webui_port) else "not_detected",
            "expected_url": f"http://127.0.0.1:{webui_port}",
            "remediation": "Run ./scripts/run_local.sh if the browser UI is closed.",
        },
        "storage": diagnostics,
        "dependencies": {
            "python": _dependency_status("python", [sys.executable, "--version"]),
            "node": _dependency_status("node", ["node", "--version"]),
            "npm": _dependency_status("npm", ["npm", "--version"]),
            "cargo": _dependency_status("cargo", ["cargo", "--version"]),
            "platform": {
                "status": "detected",
                "version": f"{platform.system()} {platform.release()}",
                "remediation": "",
            },
        },
        "ports": {
            "api": {
                "host": "127.0.0.1",
                "port": api_port,
                "status": "bound_by_current_api",
                "remediation": "",
            },
            "webui": {
                "host": "127.0.0.1",
                "port": webui_port,
                "status": "open" if _tcp_open("127.0.0.1", webui_port) else "not_open",
                "remediation": "Run ./scripts/run_local.sh to start the WebUI.",
            },
        },
        "activitywatch": {
            "enabled": activitywatch_enabled,
            "status": _activitywatch_status(activitywatch_enabled),
            "remediation": "Enable ActivityWatch import only when the user explicitly wants localhost ActivityWatch data.",
        },
        "recording": recording_manager.status(),
        "privacy_evidence": privacy_capture_evidence(),
        "guardrails": {
            "license_policy": {
                "status": "available",
                "command": "./scripts/check_licenses.sh",
                "remediation": "Run diagnostics checks or ./scripts/check_licenses.sh.",
            },
            "local_network_policy": {
                "status": "available",
                "command": "./scripts/check_no_external_network.sh",
                "remediation": "Run diagnostics checks or ./scripts/check_no_external_network.sh.",
            },
        },
        "runtime_policy": {
            "local_only": True,
            "external_network": "blocked_by_policy",
            "llm_supported": False,
            "remote_reporting": False,
        },
        "remediation": [
            "Run ./scripts/install_mac.sh when dependencies are missing.",
            "Run ./scripts/run_local.sh when API or WebUI ports are not open.",
            "Use Settings to keep ActivityWatch disabled unless explicitly needed.",
            "Run diagnostics checks before sharing exports or releases.",
        ],
    }


def privacy_capture_evidence() -> dict[str, Any]:
    prohibited = [
        ("keystrokes", "No keyboard hooks, input-monitoring APIs, or key event capture are implemented."),
        ("typed_text", "Collectors do not read form values, document text, clipboard contents, or page body text."),
        ("window_titles", "Native recording stores an empty window_title and does not request title metadata."),
        ("urls", "Native recording stores an empty URL; CSV/JSON imports are masked by the privacy pipeline."),
        ("screenshots", "No screenshot or screen-recording API is called by runtime collectors."),
        ("audio_camera", "No microphone or camera API is called by runtime collectors."),
        ("remote_reporting", "Runtime policy forbids remote event reporting, crash uploaders, analytics, and update checks."),
    ]
    return {
        "status": "passed",
        "capture_scope": "frontmost_app_only",
        "summary": "Runtime recording is limited to frontmost application name, bundle identifier, timestamps, and duration.",
        "items": [
            {
                "name": name,
                "status": "not_collected",
                "evidence": evidence,
            }
            for name, evidence in prohibited
        ],
    }


def run_diagnostic_checks() -> dict[str, Any]:
    return {
        "license_policy": _run_guardrail_script("check_licenses.sh"),
        "local_network_policy": _run_guardrail_script("check_no_external_network.sh"),
    }


def _dependency_status(name: str, command: list[str]) -> dict[str, str]:
    executable = command[0]
    if not shutil.which(executable) and executable != sys.executable:
        return {
            "status": "missing",
            "version": "",
            "remediation": f"Install {name} with ./scripts/install_mac.sh.",
        }
    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=3,
            env=sanitized_subprocess_environment(),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {"status": "error", "version": "", "remediation": str(exc)}
    version = (result.stdout or result.stderr).strip().splitlines()[0] if (result.stdout or result.stderr).strip() else ""
    return {
        "status": "installed" if result.returncode == 0 else "error",
        "version": version,
        "remediation": "" if result.returncode == 0 else f"Re-run ./scripts/install_mac.sh for {name}.",
    }


def _activitywatch_status(enabled: bool) -> str:
    if not enabled:
        return "disabled"
    return "reachable" if _tcp_open("127.0.0.1", 5600) else "not_reachable"


def _tcp_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.15):
            return True
    except OSError:
        return False


def _run_guardrail_script(script_name: str) -> dict[str, object]:
    root_dir = Path(__file__).resolve().parents[4]
    script_path = root_dir / "scripts" / script_name
    if not script_path.exists():
        return {"status": "missing", "command": f"./scripts/{script_name}", "output": "", "remediation": "Restore the diagnostics script."}
    try:
        result = subprocess.run(
            [str(script_path)],
            cwd=root_dir,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
            env=sanitized_subprocess_environment(),
        )
    except subprocess.TimeoutExpired:
        return {"status": "timeout", "command": f"./scripts/{script_name}", "output": "", "remediation": "Run the script manually for full output."}
    output = "\n".join((result.stdout + result.stderr).strip().splitlines()[-20:])
    return {
        "status": "passed" if result.returncode == 0 else "failed",
        "command": f"./scripts/{script_name}",
        "exit_code": result.returncode,
        "output": output,
        "remediation": "" if result.returncode == 0 else "Review the script output and remove blocked dependencies or network integrations.",
    }


if FastAPI is not None:
    app = FastAPI(title="OpsMineFlow Local API", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_webui_origins(),
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


def _forbidden(message: str) -> Exception:
    if FastAPI is not None:
        return HTTPException(status_code=403, detail=message)
    return PermissionError(message)


if app is not None:

    @app.get("/health")
    def health() -> dict[str, Any]:
        return create_api_snapshot()["health"]

    @app.get("/runtime/health")
    def runtime_health() -> dict[str, Any]:
        return create_runtime_health()

    @app.get("/diagnostics")
    def diagnostics() -> dict[str, Any]:
        return create_diagnostics()

    @app.post("/diagnostics/checks")
    def diagnostics_checks() -> dict[str, Any]:
        return run_diagnostic_checks()

    @app.get("/settings")
    def settings() -> dict[str, object]:
        return default_store().get_settings()

    @app.get("/import/history")
    def import_history() -> list[dict[str, object]]:
        return default_store().list_import_history()

    @app.get("/recording/status")
    def recording_status() -> dict[str, Any]:
        return recording_manager.status()

    @app.post("/recording/start")
    def recording_start(request: RecordingStartRequest) -> dict[str, Any]:
        try:
            return recording_manager.start(request.case_id, request.activity_label, request.consent)
        except (ValueError, RuntimeError) as exc:
            raise _bad_request(str(exc))

    @app.post("/recording/stop")
    def recording_stop() -> dict[str, Any]:
        return recording_manager.stop(default_store())

    @app.post("/recording/pause")
    def recording_pause(request: RecordingPauseRequest) -> dict[str, Any]:
        try:
            return recording_manager.pause(request.reason)
        except ValueError as exc:
            raise _bad_request(str(exc))

    @app.post("/recording/resume")
    def recording_resume() -> dict[str, Any]:
        try:
            return recording_manager.resume()
        except ValueError as exc:
            raise _bad_request(str(exc))

    @app.post("/recording/events")
    def recording_events(
        request: RecordingEventRequest,
        x_opsmineflow_session: str = Header(default=""),
    ) -> dict[str, Any]:
        try:
            return recording_manager.ingest(x_opsmineflow_session, request.model_dump(), default_store())
        except PermissionError as exc:
            raise _forbidden(str(exc))
        except ValueError as exc:
            raise _bad_request(str(exc))

    @app.post("/recording/heartbeat")
    def recording_heartbeat(
        request: RecordingHeartbeatRequest,
        x_opsmineflow_session: str = Header(default=""),
    ) -> dict[str, Any]:
        try:
            return recording_manager.heartbeat(x_opsmineflow_session, request.session_id, request.current_app)
        except PermissionError as exc:
            raise _forbidden(str(exc))

    @app.post("/import/preview")
    def import_preview(request: ImportPreviewRequest) -> dict[str, Any]:
        try:
            return create_import_preview(
                request.format,
                request.path,
                request.mapping,
                request.date_format,
                request.timezone,
            )
        except FileNotFoundError as exc:
            raise _not_found(str(exc))
        except ValueError as exc:
            raise _bad_request(str(exc))

    @app.post("/import/activitywatch-preview")
    def preview_activitywatch(request: ActivityWatchPreviewRequest) -> dict[str, Any]:
        try:
            return create_activitywatch_preview(request.enabled, request.base_url)
        except ValueError as exc:
            raise _bad_request(str(exc))

    @app.post("/settings")
    def update_settings(request: SettingsRequest) -> dict[str, object]:
        return default_store().update_settings(request.model_dump(exclude_none=True))

    @app.post("/import/csv")
    def import_csv(request: PathImportRequest) -> dict[str, Any]:
        try:
            return import_path_into_store(
                "csv",
                request.path,
                mapping=request.mapping,
                date_format=request.date_format,
                timezone_name=request.timezone,
            )
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
        try:
            return import_activitywatch_into_store(request.enabled, request.base_url, request.mode)
        except ValueError as exc:
            raise _bad_request(str(exc))

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

    @app.post("/events/activity")
    def update_event_activity(request: EventActivityUpdateRequest) -> dict[str, Any]:
        try:
            return {"event": default_store().update_event_activity(request.event_id, request.activity)}
        except KeyError:
            raise _not_found("Event was not found")
        except ValueError as exc:
            raise _bad_request(str(exc))

    @app.post("/events/exclude")
    def exclude_event(request: EventExcludeRequest) -> dict[str, Any]:
        try:
            return default_store().exclude_event(request.event_id)
        except KeyError:
            raise _not_found("Event was not found")

    @app.post("/events/quality-review")
    def review_event_quality(request: EventQualityReviewRequest) -> dict[str, Any]:
        try:
            return default_store().set_event_quality_review(request.event_id, request.status)
        except KeyError:
            raise _not_found("Event was not found")
        except ValueError as exc:
            raise _bad_request(str(exc))

    @app.post("/events/split")
    def split_event(request: EventSplitRequest) -> dict[str, Any]:
        try:
            return default_store().split_event(
                request.event_id,
                request.split_after_seconds,
                request.first_activity,
                request.second_activity,
            )
        except KeyError:
            raise _not_found("Event was not found")
        except ValueError as exc:
            raise _bad_request(str(exc))

    @app.post("/events/merge")
    def merge_events(request: EventMergeRequest) -> dict[str, Any]:
        try:
            return default_store().merge_adjacent_events(
                request.first_event_id,
                request.second_event_id,
                request.activity,
            )
        except KeyError:
            raise _not_found("Event was not found")
        except ValueError as exc:
            raise _bad_request(str(exc))

    @app.post("/data/delete")
    def delete_data() -> dict[str, Any]:
        recording_manager.stop(default_store())
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

    @app.get("/analytics/event-quality")
    def analytics_event_quality() -> dict[str, Any]:
        return create_event_quality_report()

    @app.post("/automation/review")
    def automation_review(request: AutomationReviewRequest) -> dict[str, str]:
        try:
            return default_store().set_automation_review(request.activity, request.status, request.note)
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
