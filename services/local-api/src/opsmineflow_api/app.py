from __future__ import annotations

import base64
import csv
import hashlib
import hmac
import json
import math
import os
import platform
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
from collections.abc import Mapping
from datetime import datetime, timezone
from io import BytesIO, StringIO
from pathlib import Path
from typing import Any
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from .auth import (
    DELETE_CHALLENGE_HEADER,
    DeleteChallengeStore,
    LocalApiPolicy,
    PROJECT_HEADER,
    RUNTIME_PROBE_CHALLENGE_HEADER,
    RequestRejected,
    consume_runtime_credentials,
)

# Launcher credentials are captured once into module-private state and removed
# from the process environment before the rest of the application is imported.
_RUNTIME_CREDENTIALS = consume_runtime_credentials()
_RUNTIME_NONCE = _RUNTIME_CREDENTIALS.nonce
_RUNTIME_PROBE_SECRET = _RUNTIME_CREDENTIALS.runtime_probe_secret
MAX_IMPORT_EVENTS = 100_000
DEFAULT_EVENT_PAGE_SIZE = 250
MAX_EVENT_PAGE_SIZE = 500
MAX_EVENT_PAGE_RESPONSE_BYTES = 3_000_000
MAX_ANALYTICS_LIST_ITEMS = 500
MAX_PROCESS_MAP_NODES = 500
MAX_PROCESS_MAP_EDGES = 1_000

from opsmineflow_drawio import build_drawio_xml
from opsmineflow_mining.analysis import correlation_for, parse_utc
from opsmineflow_mining import (
    StandardEvent,
    MiningConfig,
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
    prepare_analysis,
    score_automation_candidates,
    suggest_csv_mapping,
)
from opsmineflow_mining.pipeline import metrics_to_dict

from .activitywatch import import_activitywatch_local
from .child_process import sanitized_subprocess_environment
from .llm_handoff import build_handoff_bundle
from .recording import recording_manager
from .storage import (
    EventStore,
    ProjectConflictError,
    ProjectNotFoundError,
    StorageCommitError,
    StoreSnapshot,
    default_store,
)

try:
    from fastapi import FastAPI, Header, HTTPException, Request
    from fastapi.responses import JSONResponse
    from fastapi.middleware.cors import CORSMiddleware
    from pydantic import BaseModel
except ModuleNotFoundError:
    FastAPI = None  # type: ignore[assignment]
    HTTPException = Exception  # type: ignore[assignment]
    CORSMiddleware = None  # type: ignore[assignment]
    Header = None  # type: ignore[assignment]
    Request = object  # type: ignore[assignment,misc]
    JSONResponse = None  # type: ignore[assignment]
    BaseModel = object  # type: ignore[assignment]


class ProjectMutationRequest(BaseModel):  # type: ignore[misc, valid-type]
    expected_revision: int | None = None


class PathImportRequest(ProjectMutationRequest):  # type: ignore[misc, valid-type]
    path: str
    mapping: dict[str, str] | None = None
    date_format: str = ""
    timezone: str = "UTC"


class ImportPreviewRequest(ProjectMutationRequest):  # type: ignore[misc, valid-type]
    format: str
    path: str
    mapping: dict[str, str] | None = None
    date_format: str = ""
    timezone: str = "UTC"


class LabelRequest(ProjectMutationRequest):  # type: ignore[misc, valid-type]
    event_id: str
    label: str


class EventActivityUpdateRequest(ProjectMutationRequest):  # type: ignore[misc, valid-type]
    event_id: str
    activity: str


class EventExcludeRequest(ProjectMutationRequest):  # type: ignore[misc, valid-type]
    event_id: str


class EventQualityReviewRequest(ProjectMutationRequest):  # type: ignore[misc, valid-type]
    event_id: str
    status: str = "approved"


class EventCaseCorrelationUpdateRequest(ProjectMutationRequest):  # type: ignore[misc, valid-type]
    event_id: str
    case_id: str
    reason: str


class EventSplitRequest(ProjectMutationRequest):  # type: ignore[misc, valid-type]
    event_id: str
    split_after_seconds: float
    first_activity: str = ""
    second_activity: str = ""


class EventMergeRequest(ProjectMutationRequest):  # type: ignore[misc, valid-type]
    first_event_id: str
    second_event_id: str
    activity: str = ""


class EventPageRequest(ProjectMutationRequest):  # type: ignore[misc, valid-type]
    offset: int = 0
    limit: int = DEFAULT_EVENT_PAGE_SIZE


class ActivityWatchImportRequest(ProjectMutationRequest):  # type: ignore[misc, valid-type]
    enabled: bool = False
    base_url: str = "http://127.0.0.1:5600"
    mode: str = "replace"


class ActivityWatchPreviewRequest(ProjectMutationRequest):  # type: ignore[misc, valid-type]
    enabled: bool = False
    base_url: str = "http://127.0.0.1:5600"


class SettingsRequest(ProjectMutationRequest):  # type: ignore[misc, valid-type]
    mask_url_paths: bool | None = None
    mask_window_titles: bool | None = None
    retention_days: int | None = None
    session_gap_minutes: int | None = None
    activitywatch_enabled: bool | None = None
    excluded_apps: list[str] | None = None
    excluded_domains: list[str] | None = None


class AutomationReviewRequest(ProjectMutationRequest):  # type: ignore[misc, valid-type]
    activity: str
    status: str
    note: str = ""


class ExportPreviewRequest(ProjectMutationRequest):  # type: ignore[misc, valid-type]
    format: str


class ExportSaveRequest(ProjectMutationRequest):  # type: ignore[misc, valid-type]
    format: str
    path: str
    overwrite_confirmed: bool = False


class RecordingStartRequest(ProjectMutationRequest):  # type: ignore[misc, valid-type]
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


class ProjectCreateRequest(BaseModel):  # type: ignore[misc, valid-type]
    display_name: str


class ProjectSelectRequest(BaseModel):  # type: ignore[misc, valid-type]
    project_id: str


class ProjectRenameRequest(ProjectMutationRequest):  # type: ignore[misc, valid-type]
    project_id: str
    display_name: str


class ProjectDeleteRequest(ProjectMutationRequest):  # type: ignore[misc, valid-type]
    project_id: str


def allowed_webui_origins() -> list[str]:
    webui_port = int(os.environ.get("OPSMINEFLOW_WEBUI_PORT", "5173"))
    return [
        f"http://127.0.0.1:{webui_port}",
        f"http://localhost:{webui_port}",
        "tauri://localhost",
    ]


def local_api_policy() -> LocalApiPolicy:
    return LocalApiPolicy(
        api_session_token=_RUNTIME_CREDENTIALS.api_session_token,
        port=int(os.environ.get("OPSMINEFLOW_API_PORT", "8765")),
        allowed_origins=set(allowed_webui_origins()),
        development_mode=os.environ.get("OPSMINEFLOW_INSECURE_BROWSER_DEV_API") == "1",
    )


LOCAL_API_POLICY = local_api_policy()
DELETE_CHALLENGES = DeleteChallengeStore()


def project_store(project_id: str, *, expected_revision: int | None = None) -> EventStore:
    """Resolve an explicit, opaque project context for one local API operation."""

    if not project_id.strip():
        raise _bad_request("Project context is required.")
    try:
        return default_store().for_project(project_id, expected_revision=expected_revision)
    except ProjectNotFoundError as exc:
        raise _not_found("Project was not found.") from exc
    except ProjectConflictError as exc:
        raise _conflict(str(exc)) from exc
    except ValueError as exc:
        raise _bad_request(str(exc)) from exc


def project_response(store: EventStore, payload: Mapping[str, object] | None = None) -> dict[str, object]:
    response = dict(payload or {})
    snapshot = store.snapshot()
    response["project_id"] = snapshot.project_id
    response["project_revision"] = snapshot.project_revision
    return response


def projects_response() -> dict[str, object]:
    workspace = default_store()
    return {
        "projects": [project.to_api_dict() for project in workspace.list_projects()],
        "active_project_id": workspace.active_project_id(),
    }


def _analysis_for_store(store: EventStore, snapshot: StoreSnapshot | None = None):
    active_snapshot = snapshot or store.snapshot()
    config = _mining_config_for_settings(active_snapshot.settings)
    return store.get_or_create_analysis(
        active_snapshot.generation,
        config,
        lambda: prepare_analysis(active_snapshot.events, config),
    )


def _mining_config_for_settings(settings: Mapping[str, object]) -> MiningConfig:
    filter_context = (
        ("excluded_apps", tuple(sorted(str(value).casefold() for value in settings.get("excluded_apps", []) if str(value).strip()))),
        ("excluded_domains", tuple(sorted(str(value).casefold() for value in settings.get("excluded_domains", []) if str(value).strip()))),
    )
    return MiningConfig(
        session_gap_minutes=int(settings.get("session_gap_minutes", 30)),
        filter_context=filter_context,
    )


def create_api_snapshot(
    store: EventStore | None = None,
    snapshot: StoreSnapshot | None = None,
) -> dict[str, Any]:
    active_store = store or default_store()
    store_snapshot = snapshot or active_store.snapshot()
    events = store_snapshot.events
    settings = store_snapshot.settings
    analysis = _analysis_for_store(active_store, store_snapshot)
    metrics = calculate_duration_metrics(analysis)
    process_map = build_directly_follows_graph(analysis)
    store_diagnostics = active_store.diagnostics()
    automation_candidates = apply_automation_reviews(
        score_automation_candidates(analysis), store_snapshot, list(analysis.events)
    )
    health = {
        **create_public_health(),
        "storage_mode": store_diagnostics["storage_mode"],
        "event_count": len(events),
    }
    return {
        "health": health,
        "events": [event_to_api_dict(event, settings) for event in events],
        "summary": metrics_to_dict(metrics),
        "analysis_receipt": analysis.receipt.to_dict(),
        "app_switching": detect_app_switches(analysis),
        "automation_candidates": automation_candidates,
        "event_quality": create_event_quality_report(active_store, store_snapshot, analysis),
        "process_map": process_map,
        "variants": analyze_variants(analysis),
        "bottlenecks": detect_bottlenecks(analysis),
        "markdown_report": append_automation_review_section(export_markdown_report(analysis), automation_candidates),
        "mermaid": export_mermaid(analysis),
        "drawio": build_drawio_xml(process_map),
    }


def create_event_page(
    offset: int = 0,
    limit: int = DEFAULT_EVENT_PAGE_SIZE,
    store: EventStore | None = None,
) -> dict[str, Any]:
    if offset < 0:
        raise ValueError("Event page offset must not be negative.")
    if limit < 1 or limit > MAX_EVENT_PAGE_SIZE:
        raise ValueError(f"Event page size must be between 1 and {MAX_EVENT_PAGE_SIZE}.")
    active_store = store or default_store()
    store_snapshot = active_store.snapshot()
    settings = store_snapshot.settings
    total = len(store_snapshot.events)
    page: list[dict[str, object]] = []
    response_bytes = 0
    for event in store_snapshot.events[offset : offset + limit]:
        record = event_to_api_dict(event, settings)
        record_bytes = len(json.dumps(record, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
        if page and response_bytes + record_bytes > MAX_EVENT_PAGE_RESPONSE_BYTES:
            break
        page.append(record)
        response_bytes += record_bytes
    return {
        "events": page,
        "offset": offset,
        "limit": len(page),
        "total": total,
        "has_more": offset + len(page) < total,
    }


def create_summary(store: EventStore | None = None) -> dict[str, Any]:
    active_store = store or default_store()
    analysis = _analysis_for_store(active_store, active_store.snapshot())
    return {**metrics_to_dict(calculate_duration_metrics(analysis)), "analysis_receipt": analysis.receipt.to_dict()}


def create_app_switching(store: EventStore | None = None) -> dict[str, Any]:
    active_store = store or default_store()
    analysis = _analysis_for_store(active_store, active_store.snapshot())
    switching = detect_app_switches(analysis)
    return {
        "transition_ranking": list(switching["transition_ranking"])[:MAX_ANALYTICS_LIST_ITEMS],
        "round_trips": list(switching["round_trips"])[:MAX_ANALYTICS_LIST_ITEMS],
        "analysis_receipt": analysis.receipt.to_dict(),
    }


def create_automation_candidates(store: EventStore | None = None) -> dict[str, Any]:
    active_store = store or default_store()
    store_snapshot = active_store.snapshot()
    analysis = _analysis_for_store(active_store, store_snapshot)
    candidates = apply_automation_reviews(
        score_automation_candidates(analysis), store_snapshot, list(analysis.events)
    )
    return {
        "candidates": candidates[:MAX_ANALYTICS_LIST_ITEMS],
        "analysis_receipt": analysis.receipt.to_dict(),
    }


def create_process_map(store: EventStore | None = None) -> dict[str, Any]:
    active_store = store or default_store()
    graph = build_directly_follows_graph(_analysis_for_store(active_store, active_store.snapshot()))
    nodes = list(graph["nodes"])[:MAX_PROCESS_MAP_NODES]
    visible_activities = {str(node["activity"]) for node in nodes}
    edges = [
        edge
        for edge in graph["edges"]
        if str(edge["source"]) in visible_activities and str(edge["target"]) in visible_activities
    ][:MAX_PROCESS_MAP_EDGES]
    return {
        "nodes": nodes,
        "edges": edges,
        "start_activities": {
            activity: count
            for activity, count in dict(graph["start_activities"]).items()
            if activity in visible_activities
        },
        "end_activities": {
            activity: count
            for activity, count in dict(graph["end_activities"]).items()
            if activity in visible_activities
        },
        "analysis_receipt": graph["analysis_receipt"],
    }


def create_markdown_report(store: EventStore | None = None) -> str:
    active_store = store or default_store()
    store_snapshot = active_store.snapshot()
    analysis = _analysis_for_store(active_store, store_snapshot)
    candidates = apply_automation_reviews(
        score_automation_candidates(analysis), store_snapshot, list(analysis.events)
    )
    return append_automation_review_section(export_markdown_report(analysis), candidates)
def create_public_health() -> dict[str, Any]:
    """Return the unauthenticated, constant-time local readiness payload."""

    return {
        "status": "ok",
        "bind": "127.0.0.1",
        "local_only": True,
        "llm_supported": False,
    }


def create_runtime_health(probe_challenge: str = "") -> dict[str, Any]:
    """Return ownership metadata only after a same-connection proof request."""

    health: dict[str, Any] = create_public_health()
    if _RUNTIME_NONCE and _RUNTIME_PROBE_SECRET and _valid_runtime_probe_challenge(probe_challenge):
        health["runtime"] = {
            "nonce": _RUNTIME_NONCE,
            "pid": os.getpid(),
            "proof": hmac.new(
                _RUNTIME_PROBE_SECRET.encode("utf-8"),
                probe_challenge.encode("ascii"),
                hashlib.sha256,
            ).hexdigest(),
        }
    return health


def _valid_runtime_probe_challenge(challenge: str) -> bool:
    return len(challenge) == 64 and all(character in "0123456789abcdef" for character in challenge)


def apply_automation_reviews(
    candidates: list[dict[str, object]],
    snapshot: StoreSnapshot,
    events: list[Any],
) -> list[dict[str, object]]:
    activity_profiles = _automation_activity_profiles(events)
    reviewed: list[dict[str, object]] = []
    for candidate in candidates:
        item = dict(candidate)
        activity = str(item.get("activity", ""))
        item.update(_automation_portfolio_fields(item, activity_profiles.get(activity, {})))
        item["review_status"] = snapshot.automation_reviews.get(activity, "unreviewed")
        item["review_note"] = snapshot.automation_review_notes.get(activity, "")
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


def create_event_quality_report(
    store: EventStore | None = None,
    snapshot: StoreSnapshot | None = None,
    analysis: Any | None = None,
) -> dict[str, Any]:
    active_store = store or default_store()
    store_snapshot = snapshot or active_store.snapshot()
    active_analysis = analysis or _analysis_for_store(active_store, store_snapshot)
    items: list[dict[str, Any]] = []
    items_by_event_id: dict[str, dict[str, Any]] = {}
    events_by_id = {event.event_id: event for event in store_snapshot.events}
    issue_totals = {
        "missing_fields": 0,
        "invalid_time": 0,
        "zero_duration": 0,
        "short_duration": 0,
        "long_duration": 0,
        "unlabeled": 0,
        "low_confidence": 0,
        "case_correlation_low_confidence": 0,
        "duration_interval_mismatch": 0,
    }
    for event in store_snapshot.events:
        status = _event_quality_status(event)
        issues = _event_quality_issues(event)
        for issue in issues:
            code = str(issue["code"])
            if code in issue_totals:
                issue_totals[code] += 1
        if issues:
            item = _quality_item(event, status, issues)
            items.append(item)
            items_by_event_id[event.event_id] = item

    for exclusion in active_analysis.exclusions:
        event = events_by_id.get(exclusion.event_id)
        if event is None:
            continue
        issue = {
            "code": f"analysis_{exclusion.reason}",
            "severity": "high",
            "label": f"Excluded from analysis: {exclusion.reason.replace('_', ' ')}.",
            "remediation": exclusion.remediation,
            "evidence": exclusion.evidence,
        }
        item = items_by_event_id.get(event.event_id)
        if item is None:
            item = _quality_item(event, "requires_correction", [issue], analysis_excluded=True)
            items.append(item)
            items_by_event_id[event.event_id] = item
        else:
            item["issues"].append(issue)
            item["analysis_excluded"] = True
            item["quality_review_status"] = "requires_correction"
            item["recommended_action"] = _quality_recommended_action(item["issues"])

    excluded_event_ids = {exclusion.event_id for exclusion in active_analysis.exclusions}
    approved_count = sum(
        1
        for event in store_snapshot.events
        if _event_quality_status(event) == "approved" and event.event_id not in excluded_event_ids
    )
    unresolved_items = [item for item in items if item["quality_review_status"] != "approved"]
    return {
        "summary": {
            "total_events": len(store_snapshot.events),
            "affected_event_count": len(unresolved_items),
            "issue_count": sum(len(item["issues"]) for item in unresolved_items),
            "approved_count": approved_count,
            **issue_totals,
        },
        "items": items,
        "analysis_receipt": active_analysis.receipt.to_dict(),
    }


def _quality_item(
    event: Any,
    status: str,
    issues: list[dict[str, str]],
    *,
    analysis_excluded: bool = False,
) -> dict[str, Any]:
    return {
        "event_id": event.event_id,
        "case_id": event.case_id,
        "case_correlation": _case_correlation_to_dict(event),
        "case_correlation_review": _case_correlation_review_to_dict(event),
        "activity": event.activity_raw,
        "app_name": event.app_name,
        "timestamp_start": event.timestamp_start,
        "timestamp_end": event.timestamp_end,
        "duration_seconds": event.duration_seconds,
        "quality_review_status": status,
        "analysis_excluded": analysis_excluded,
        "issues": issues,
        "recommended_action": _quality_recommended_action(issues),
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

    interval_seconds: float | None = None
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
        else:
            interval_seconds = (end - start).total_seconds()
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
    if not math.isfinite(duration) or duration <= 0:
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
    if interval_seconds is not None and interval_seconds > 0 and abs(duration - interval_seconds) > 1.0:
        issues.append(
            {
                "code": "duration_interval_mismatch",
                "severity": "high",
                "label": "Source duration does not match the timestamp interval.",
                "remediation": "Correct the source duration or timestamps and import again.",
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
    try:
        metadata = json.loads(event.metadata_json) if event.metadata_json else {}
    except json.JSONDecodeError:
        metadata = {}
    correlation = metadata.get("opsmineflow_case_correlation") if isinstance(metadata, dict) else None
    if isinstance(correlation, dict) and (
        str(correlation.get("origin") or "").lower() in {"inferred", "unassigned"}
        or str(correlation.get("confidence") or "").lower() == "low"
    ):
        issues.append(
            {
                "code": "case_correlation_low_confidence",
                "severity": "high",
                "label": "Case correlation is inferred or unassigned.",
            "remediation": "Enter a reviewed case ID and a short reason, or keep this event separate before relying on a process flow.",
            }
        )
    return issues


def _quality_recommended_action(issues: list[dict[str, str]]) -> str:
    codes = {issue["code"] for issue in issues}
    analysis_codes = {code.removeprefix("analysis_") for code in codes if code.startswith("analysis_")}
    if analysis_codes & {"duplicate_event", "conflicting_source_event_id", "invalid_timestamp", "invalid_duration"}:
        return "repair_source"
    if analysis_codes & {"negative_interval", "zero_duration", "duration_interval_mismatch", "overlapping_or_parallel_session", "idle_event"}:
        return "split_or_exclude"
    if "missing_fields" in codes or "unlabeled" in codes or "low_confidence" in codes:
        return "edit_label"
    if "case_correlation_low_confidence" in codes:
        return "edit_case_correlation"
    if "long_duration" in codes or "zero_duration" in codes or "invalid_time" in codes or "duration_interval_mismatch" in codes:
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
    return parse_utc(value)


def event_to_api_dict(event: Any, settings: dict[str, object]) -> dict[str, object]:
    return {
        "event_id": event.event_id,
        "case_id": event.case_id,
        "user_hash": event.user_hash,
        "app_name": event.app_name,
        "window_title_masked": event.window_title_masked
        if settings.get("mask_window_titles", True)
        else event.window_title,
        "url_masked": event.url_masked if settings.get("mask_url_paths", True) else event.url,
        "domain": event.domain,
        "activity_raw": event.activity_raw,
        "timestamp_start": event.timestamp_start,
        "timestamp_end": event.timestamp_end,
        "duration_seconds": event.duration_seconds,
        "confidential_flag": event.confidential_flag,
        "quality_review_status": _event_quality_status(event),
        "case_correlation": _case_correlation_to_dict(event),
        "case_correlation_review": _case_correlation_review_to_dict(event),
    }


def _case_correlation_to_dict(event: Any) -> dict[str, str]:
    correlation = correlation_for(event)
    return {
        "origin": correlation.origin,
        "strategy": correlation.strategy,
        "confidence": correlation.confidence,
        "evidence": correlation.evidence,
    }


def _case_correlation_review_to_dict(event: Any) -> dict[str, str] | None:
    try:
        metadata = json.loads(event.metadata_json) if event.metadata_json else {}
    except json.JSONDecodeError:
        return None
    review = metadata.get("opsmineflow_case_correlation_review") if isinstance(metadata, dict) else None
    if not isinstance(review, dict):
        return None
    required = ("action", "previous_case_id", "reason", "operator", "changed_at")
    if any(not isinstance(review.get(key), str) for key in required):
        return None
    return {key: str(review[key]) for key in required}


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
            return load_events_from_csv_with_mapping(
                path,
                mapping,
                date_format=date_format,
                timezone_name=timezone_name,
                max_events=MAX_IMPORT_EVENTS,
            )
        return load_events_from_csv(path, max_events=MAX_IMPORT_EVENTS)
    if format_name == "json":
        return load_events_from_json(path, max_events=MAX_IMPORT_EVENTS)
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
        "display_name": _safe_display_name(path),
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
    active_store.replace(events, import_source=format_name, import_path=_safe_display_name(path))
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
    pre_import_snapshot = active_store.snapshot()
    existing_ids = {event.event_id for event in pre_import_snapshot.events}
    importable_events = active_store.filter_events(list(events), pre_import_snapshot)

    if normalized_mode == "replace":
        active_store.replace(events, import_source="activitywatch_local", import_path=base_url)
        imported_events = len(active_store.snapshot().events)
        skipped_duplicates = 0
    else:
        imported_events = active_store.append(
            events,
            import_source=f"activitywatch_local_{normalized_mode}",
            import_path=base_url,
        )
        skipped_duplicates = sum(1 for event in importable_events if event.event_id in existing_ids)

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
    store_snapshot = store.snapshot()
    filtered_events = store.filter_events(list(events), store_snapshot)
    existing_ids = {event.event_id for event in store_snapshot.events}
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
    store_snapshot = active_store.snapshot()
    if format_name == "llm-handoff":
        bundle = build_handoff_bundle(
            store_snapshot.events,
            store_snapshot.automation_reviews,
            store_snapshot.automation_review_notes,
            config=_mining_config_for_settings(store_snapshot.settings),
        )
        content: str | bytes = bundle.content
        extension = "zip"
        filename = "opsmineflow-mermaid-handoff.zip"
        preview = bundle.preview
        warning = (
            "This ZIP is a manual Mermaid handoff only. It contains aggregate evidence, "
            "not raw event rows; review it before sharing outside your organization."
        )
    else:
        snapshot = create_api_snapshot(active_store, store_snapshot)
        if format_name == "markdown":
            content = str(snapshot["markdown_report"])
            extension = "md"
        elif format_name == "json":
            content = json_dumps({"snapshot": snapshot})
            extension = "json"
        elif format_name == "csv":
            receipt = snapshot.get("analysis_receipt")
            if not isinstance(receipt, dict):
                raise RuntimeError("CSV export requires an analysis receipt.")
            content = build_csv_export_bundle(snapshot["events"], receipt)
            extension = "zip"
        elif format_name == "mermaid":
            content = str(snapshot["mermaid"])
            extension = "mmd"
        elif format_name == "drawio":
            content = str(snapshot["drawio"])
            extension = "drawio"
        else:
            raise ValueError("Export format must be markdown, json, csv, mermaid, drawio, or llm-handoff.")
        filename = "opsmineflow-events-with-analysis-receipt.zip" if format_name == "csv" else f"opsmineflow-export.{extension}"
        preview = (
            "ZIP bundle containing events.csv and analysis-receipt.json."
            if format_name == "csv"
            else str(content)[:2000]
        )
        warning = "Review masked fields and confidential flags before sharing this export."

    byte_content = content if isinstance(content, bytes) else content.encode("utf-8")
    return {
        "format": format_name,
        "extension": extension,
        "filename": filename,
        "content": content,
        "byte_size": len(byte_content),
        "preview": preview,
        "confidential_count": sum(1 for event in store_snapshot.events if event.confidential_flag),
        "warning": warning,
    }


def export_llm_handoff_payload(store: EventStore | None = None) -> dict[str, str]:
    """Encode the locally generated ZIP for the development-only browser download path."""

    artifact = create_export_artifact("llm-handoff", store=store)
    content = artifact["content"]
    if not isinstance(content, bytes):
        raise ValueError("LLM handoff export must be a ZIP file.")
    return {
        "filename": str(artifact["filename"]),
        "zip_base64": base64.b64encode(content).decode("ascii"),
    }


def save_export_artifact(
    format_name: str,
    path_value: str,
    store: EventStore | None = None,
    overwrite_confirmed: bool = False,
) -> dict[str, Any]:
    if not path_value.strip():
        raise ValueError("Export path is required.")
    artifact = create_export_artifact(format_name, store=store)
    path = Path(path_value).expanduser()
    if not path.suffix:
        path = path.with_suffix(f".{artifact['extension']}")
    try:
        parent_metadata = os.lstat(path.parent)
    except OSError as exc:
        raise ValueError("Choose an existing local folder before saving the export.") from exc
    if stat.S_ISLNK(parent_metadata.st_mode) or not stat.S_ISDIR(parent_metadata.st_mode):
        raise ValueError("Choose a regular local folder, not a link, before saving the export.")
    try:
        target_metadata = os.lstat(path)
    except FileNotFoundError:
        target_metadata = None
    if target_metadata is not None and (
        stat.S_ISLNK(target_metadata.st_mode) or not stat.S_ISREG(target_metadata.st_mode)
    ):
        raise ValueError("Choose a regular export filename, not a folder or link.")
    if not path.parent.is_dir():
        raise ValueError("Choose an existing local folder before saving the export.")
    if target_metadata is not None and not overwrite_confirmed:
        raise ValueError("Confirm replacement in the save dialog before overwriting an existing file.")
    file_descriptor, temporary_path = tempfile.mkstemp(prefix=".opsmineflow-export-", dir=path.parent)
    try:
        content = artifact["content"]
        if isinstance(content, bytes):
            with os.fdopen(file_descriptor, "wb") as export_file:
                export_file.write(content)
                export_file.flush()
                os.fsync(export_file.fileno())
        else:
            with os.fdopen(file_descriptor, "w", encoding="utf-8") as export_file:
                export_file.write(content)
                export_file.flush()
                os.fsync(export_file.fileno())
        os.replace(temporary_path, path)
        _fsync_directory(path.parent)
    except Exception:
        Path(temporary_path).unlink(missing_ok=True)
        raise
    return {
        "saved": True,
        "format": artifact["format"],
        "filename": path.name,
        "byte_size": artifact["byte_size"],
        "warning": artifact["warning"],
    }


def _safe_display_name(path: Path) -> str:
    name = path.name.strip()
    return name or "Selected file"


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


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


def build_csv_export_bundle(events: list[dict[str, object]], receipt: dict[str, object]) -> bytes:
    """Package raw CSV rows with a stable, privacy-safe analysis receipt.

    CSV has no portable metadata channel. A deterministic local ZIP keeps the
    event table compatible with spreadsheet tooling while ensuring a recipient
    cannot lose the used/excluded/session interpretation.
    """

    receipt_payload = {
        "scope": "all current local events; no saved analysis filter is applied",
        "analysis_receipt": receipt,
    }
    entries = {
        "events.csv": events_to_csv(events).encode("utf-8"),
        "analysis-receipt.json": json.dumps(
            receipt_payload, ensure_ascii=False, sort_keys=True, indent=2
        ).encode("utf-8"),
    }
    buffer = BytesIO()
    with ZipFile(buffer, "w", compression=ZIP_DEFLATED) as archive:
        for name, data in entries.items():
            info = ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = ZIP_DEFLATED
            archive.writestr(info, data)
    return buffer.getvalue()


def json_dumps(payload: object) -> str:
    import json

    return json.dumps(payload, ensure_ascii=False, indent=2)


def create_diagnostics(store: EventStore | None = None) -> dict[str, Any]:
    active_store = store or default_store()
    store_snapshot = active_store.snapshot()
    diagnostics = active_store.diagnostics(store_snapshot)
    settings = store_snapshot.settings
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
            "remediation": "Open or restart the managed OpsMineFlow desktop app.",
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
                "remediation": "Open or restart the managed OpsMineFlow desktop app.",
            },
        },
        "activitywatch": {
            "enabled": activitywatch_enabled,
            "status": _activitywatch_status(activitywatch_enabled),
            "remediation": "Enable ActivityWatch import only when the user explicitly wants localhost ActivityWatch data.",
        },
        "recording": recording_manager.status(store_snapshot.project_id),
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
            "Open or restart the managed OpsMineFlow desktop app when its local runtime is unavailable.",
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
    app = FastAPI(title="OpsMineFlow Local API", version="0.1.0", docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_webui_origins(),
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["content-type", DELETE_CHALLENGE_HEADER, PROJECT_HEADER],
    )

    @app.middleware("http")
    async def enforce_local_api_policy(request: Request, call_next: Any):
        try:
            LOCAL_API_POLICY.authorize(
                request.method,
                request.url.path,
                request.headers,
                request.headers.get("content-length"),
            )
        except RequestRejected as exc:
            assert JSONResponse is not None
            return JSONResponse({"error": exc.message}, status_code=exc.status_code)
        return await call_next(request)

    @app.exception_handler(StorageCommitError)
    async def storage_commit_error(_request: Request, exc: StorageCommitError):
        assert JSONResponse is not None
        return JSONResponse({"error": exc.to_api_dict()}, status_code=503)

    @app.exception_handler(ProjectConflictError)
    async def project_conflict_error(_request: Request, exc: ProjectConflictError):
        assert JSONResponse is not None
        return JSONResponse({"error": str(exc)}, status_code=409)

    @app.exception_handler(ProjectNotFoundError)
    async def project_not_found_error(_request: Request, _exc: ProjectNotFoundError):
        assert JSONResponse is not None
        return JSONResponse({"error": "Project was not found."}, status_code=404)
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


def _conflict(message: str) -> Exception:
    if FastAPI is not None:
        return HTTPException(status_code=409, detail=message)
    return ProjectConflictError(message)


def _forbidden(message: str) -> Exception:
    if FastAPI is not None:
        return HTTPException(status_code=403, detail=message)
    return PermissionError(message)


if app is not None:

    @app.get("/health")
    def health() -> dict[str, Any]:
        return create_public_health()

    @app.get("/runtime/health")
    def runtime_health(request: Request) -> dict[str, Any]:
        return create_runtime_health(request.headers.get(RUNTIME_PROBE_CHALLENGE_HEADER, ""))

    @app.get("/projects")
    def projects() -> dict[str, object]:
        return projects_response()

    @app.post("/projects")
    def create_project_endpoint(request: ProjectCreateRequest) -> dict[str, object]:
        project = default_store().create_project(request.display_name)
        return {**projects_response(), "project": project.to_api_dict()}

    @app.post("/projects/select")
    def select_project_endpoint(request: ProjectSelectRequest) -> dict[str, object]:
        project = default_store().select_project(request.project_id)
        return {**projects_response(), "project": project.to_api_dict()}

    @app.post("/projects/rename")
    def rename_project_endpoint(request: ProjectRenameRequest) -> dict[str, object]:
        try:
            project = default_store().rename_project(
                request.project_id,
                request.display_name,
                expected_revision=request.expected_revision,
            )
        except ProjectConflictError as exc:
            raise _conflict(str(exc))
        except ProjectNotFoundError:
            raise _not_found("Project was not found.")
        except ValueError as exc:
            raise _bad_request(str(exc))
        return {**projects_response(), "project": project.to_api_dict()}

    @app.post("/projects/delete")
    def delete_project_endpoint(request: ProjectDeleteRequest) -> dict[str, object]:
        try:
            with recording_manager.project_deletion_guard(request.project_id):
                replacement_project_id = default_store().delete_project(
                    request.project_id,
                    expected_revision=request.expected_revision,
                )
        except ProjectConflictError as exc:
            raise _conflict(str(exc))
        except ProjectNotFoundError:
            raise _not_found("Project was not found.")
        except ValueError as exc:
            raise _bad_request(str(exc))
        return {**projects_response(), "deleted_project_id": request.project_id, "replacement_project_id": replacement_project_id}

    @app.get("/diagnostics")
    def diagnostics(x_opsmineflow_project: str = Header(default="")) -> dict[str, Any]:
        store = project_store(x_opsmineflow_project)
        return project_response(store, create_diagnostics(store))

    @app.post("/diagnostics/checks")
    def diagnostics_checks(x_opsmineflow_project: str = Header(default="")) -> dict[str, Any]:
        store = project_store(x_opsmineflow_project)
        return project_response(store, run_diagnostic_checks())

    @app.get("/settings")
    def settings(x_opsmineflow_project: str = Header(default="")) -> dict[str, object]:
        store = project_store(x_opsmineflow_project)
        return project_response(store, store.get_settings())

    @app.get("/import/history")
    def import_history(x_opsmineflow_project: str = Header(default="")) -> dict[str, object]:
        store = project_store(x_opsmineflow_project)
        return project_response(store, {"imports": store.list_import_history()})

    @app.get("/recording/status")
    def recording_status(x_opsmineflow_project: str = Header(default="")) -> dict[str, Any]:
        store = project_store(x_opsmineflow_project)
        return project_response(store, recording_manager.status(store.project_id))

    @app.post("/recording/start")
    def recording_start(
        request: RecordingStartRequest,
        x_opsmineflow_project: str = Header(default=""),
    ) -> dict[str, Any]:
        try:
            store = project_store(x_opsmineflow_project, expected_revision=request.expected_revision)
            return project_response(
                store,
                recording_manager.start(request.case_id, request.activity_label, request.consent, store=store),
            )
        except (ValueError, RuntimeError) as exc:
            raise _bad_request(str(exc))

    @app.post("/recording/stop")
    def recording_stop(
        request: ProjectMutationRequest,
        x_opsmineflow_project: str = Header(default=""),
    ) -> dict[str, Any]:
        # Ingested recording events advance the project revision. Stopping the
        # session must resolve the current revision after validating the
        # immutable project context, otherwise a start-time revision traps the
        # user in an un-stoppable active session.
        store = project_store(x_opsmineflow_project)
        return project_response(store, recording_manager.stop(store))

    @app.post("/recording/pause")
    def recording_pause(
        request: RecordingPauseRequest,
        x_opsmineflow_project: str = Header(default=""),
    ) -> dict[str, Any]:
        try:
            store = project_store(x_opsmineflow_project)
            return project_response(store, recording_manager.pause(request.reason, project_id=store.project_id))
        except ValueError as exc:
            raise _bad_request(str(exc))

    @app.post("/recording/resume")
    def recording_resume(
        request: ProjectMutationRequest,
        x_opsmineflow_project: str = Header(default=""),
    ) -> dict[str, Any]:
        try:
            store = project_store(x_opsmineflow_project)
            return project_response(store, recording_manager.resume(project_id=store.project_id))
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
    def import_preview(
        request: ImportPreviewRequest,
        x_opsmineflow_project: str = Header(default=""),
    ) -> dict[str, Any]:
        try:
            store = project_store(x_opsmineflow_project, expected_revision=request.expected_revision)
            return project_response(store, create_import_preview(
                request.format,
                request.path,
                request.mapping,
                request.date_format,
                request.timezone,
            ))
        except FileNotFoundError as exc:
            raise _not_found(str(exc))
        except ValueError as exc:
            raise _bad_request(str(exc))

    @app.post("/import/activitywatch-preview")
    def preview_activitywatch(
        request: ActivityWatchPreviewRequest,
        x_opsmineflow_project: str = Header(default=""),
    ) -> dict[str, Any]:
        try:
            store = project_store(x_opsmineflow_project, expected_revision=request.expected_revision)
            return project_response(store, create_activitywatch_preview(request.enabled, request.base_url, store))
        except ValueError as exc:
            raise _bad_request(str(exc))

    @app.post("/settings")
    def update_settings(
        request: SettingsRequest,
        x_opsmineflow_project: str = Header(default=""),
    ) -> dict[str, object]:
        store = project_store(x_opsmineflow_project, expected_revision=request.expected_revision)
        updates = request.model_dump(exclude_none=True)
        updates.pop("expected_revision", None)
        return project_response(store, store.update_settings(updates))

    @app.post("/import/csv")
    def import_csv(
        request: PathImportRequest,
        x_opsmineflow_project: str = Header(default=""),
    ) -> dict[str, Any]:
        try:
            store = project_store(x_opsmineflow_project, expected_revision=request.expected_revision)
            return project_response(store, import_path_into_store(
                "csv",
                request.path,
                mapping=request.mapping,
                date_format=request.date_format,
                timezone_name=request.timezone,
                store=store,
            ))
        except FileNotFoundError as exc:
            raise _not_found(str(exc))

    @app.post("/import/json")
    def import_json(
        request: PathImportRequest,
        x_opsmineflow_project: str = Header(default=""),
    ) -> dict[str, Any]:
        try:
            store = project_store(x_opsmineflow_project, expected_revision=request.expected_revision)
            return project_response(store, import_path_into_store("json", request.path, store=store))
        except FileNotFoundError as exc:
            raise _not_found(str(exc))

    @app.post("/import/activitywatch-local")
    def import_activitywatch(
        request: ActivityWatchImportRequest,
        x_opsmineflow_project: str = Header(default=""),
    ) -> dict[str, Any]:
        try:
            store = project_store(x_opsmineflow_project, expected_revision=request.expected_revision)
            return project_response(
                store,
                import_activitywatch_into_store(request.enabled, request.base_url, request.mode, store=store),
            )
        except ValueError as exc:
            raise _bad_request(str(exc))

    @app.get("/events")
    def events(x_opsmineflow_project: str = Header(default="")) -> dict[str, Any]:
        store = project_store(x_opsmineflow_project)
        return project_response(store, {"events": create_event_page(0, MAX_EVENT_PAGE_SIZE, store)["events"]})

    @app.post("/events/page")
    def event_page(
        request: EventPageRequest,
        x_opsmineflow_project: str = Header(default=""),
    ) -> dict[str, Any]:
        try:
            store = project_store(x_opsmineflow_project, expected_revision=request.expected_revision)
            return project_response(store, create_event_page(request.offset, request.limit, store))
        except ValueError as exc:
            raise _bad_request(str(exc))

    @app.post("/events/label")
    def label_event(
        request: LabelRequest,
        x_opsmineflow_project: str = Header(default=""),
    ) -> dict[str, Any]:
        try:
            store = project_store(x_opsmineflow_project, expected_revision=request.expected_revision)
            store.set_label(request.event_id, request.label)
        except KeyError:
            raise _not_found("Event was not found")
        return project_response(store, {"event_id": request.event_id, "label": request.label})

    @app.post("/events/activity")
    def update_event_activity(
        request: EventActivityUpdateRequest,
        x_opsmineflow_project: str = Header(default=""),
    ) -> dict[str, Any]:
        try:
            store = project_store(x_opsmineflow_project, expected_revision=request.expected_revision)
            return project_response(store, {"event": store.update_event_activity(request.event_id, request.activity)})
        except KeyError:
            raise _not_found("Event was not found")
        except ProjectConflictError as exc:
            raise _conflict(str(exc))
        except ValueError as exc:
            raise _bad_request(str(exc))

    @app.post("/events/exclude")
    def exclude_event(
        request: EventExcludeRequest,
        x_opsmineflow_project: str = Header(default=""),
    ) -> dict[str, Any]:
        try:
            store = project_store(x_opsmineflow_project, expected_revision=request.expected_revision)
            return project_response(store, store.exclude_event(request.event_id))
        except KeyError:
            raise _not_found("Event was not found")
        except ProjectConflictError as exc:
            raise _conflict(str(exc))

    @app.post("/events/quality-review")
    def review_event_quality(
        request: EventQualityReviewRequest,
        x_opsmineflow_project: str = Header(default=""),
    ) -> dict[str, Any]:
        try:
            store = project_store(x_opsmineflow_project, expected_revision=request.expected_revision)
            return project_response(store, store.set_event_quality_review(request.event_id, request.status))
        except KeyError:
            raise _not_found("Event was not found")
        except ProjectConflictError as exc:
            raise _conflict(str(exc))
        except ValueError as exc:
            raise _bad_request(str(exc))

    @app.post("/events/case-correlation")
    def update_event_case_correlation(
        request: EventCaseCorrelationUpdateRequest,
        x_opsmineflow_project: str = Header(default=""),
    ) -> dict[str, Any]:
        try:
            store = project_store(x_opsmineflow_project, expected_revision=request.expected_revision)
            event = store.update_event_case_correlation(request.event_id, request.case_id, request.reason)
        except KeyError:
            raise _not_found("Event was not found")
        except ProjectConflictError as exc:
            raise _conflict(str(exc))
        except ValueError as exc:
            raise _bad_request(str(exc))
        return project_response(store, {"event": event_to_api_dict(event, store.get_settings())})

    @app.post("/events/split")
    def split_event(
        request: EventSplitRequest,
        x_opsmineflow_project: str = Header(default=""),
    ) -> dict[str, Any]:
        try:
            store = project_store(x_opsmineflow_project, expected_revision=request.expected_revision)
            return project_response(store, store.split_event(
                request.event_id,
                request.split_after_seconds,
                request.first_activity,
                request.second_activity,
            ))
        except KeyError:
            raise _not_found("Event was not found")
        except ProjectConflictError as exc:
            raise _conflict(str(exc))
        except ValueError as exc:
            raise _bad_request(str(exc))

    @app.post("/events/merge")
    def merge_events(
        request: EventMergeRequest,
        x_opsmineflow_project: str = Header(default=""),
    ) -> dict[str, Any]:
        try:
            store = project_store(x_opsmineflow_project, expected_revision=request.expected_revision)
            return project_response(store, store.merge_adjacent_events(
                request.first_event_id,
                request.second_event_id,
                request.activity,
            ))
        except KeyError:
            raise _not_found("Event was not found")
        except ProjectConflictError as exc:
            raise _conflict(str(exc))
        except ValueError as exc:
            raise _bad_request(str(exc))

    @app.post("/data/delete")
    def delete_data(
        request: ProjectMutationRequest,
        x_opsmineflow_delete_challenge: str = Header(default=""),
        x_opsmineflow_project: str = Header(default=""),
    ) -> dict[str, Any]:
        if not DELETE_CHALLENGES.consume(x_opsmineflow_delete_challenge):
            raise _forbidden("delete challenge is invalid or expired")
        store = project_store(x_opsmineflow_project, expected_revision=request.expected_revision)
        if recording_manager.status(store.project_id).get("active"):
            recording_manager.stop(store, record_import=False)
        store.clear()
        return project_response(store, {"deleted": True})

    @app.post("/data/delete/challenge")
    def delete_challenge() -> dict[str, str]:
        return {"challenge": DELETE_CHALLENGES.issue()}

    @app.get("/analytics/summary")
    def analytics_summary(x_opsmineflow_project: str = Header(default="")) -> dict[str, Any]:
        store = project_store(x_opsmineflow_project)
        return project_response(store, create_summary(store))

    @app.get("/analytics/app-switching")
    def analytics_app_switching(x_opsmineflow_project: str = Header(default="")) -> dict[str, Any]:
        store = project_store(x_opsmineflow_project)
        return project_response(store, create_app_switching(store))

    @app.get("/analytics/automation-candidates")
    def analytics_automation_candidates(x_opsmineflow_project: str = Header(default="")) -> dict[str, Any]:
        store = project_store(x_opsmineflow_project)
        return project_response(store, create_automation_candidates(store))

    @app.get("/analytics/event-quality")
    def analytics_event_quality(x_opsmineflow_project: str = Header(default="")) -> dict[str, Any]:
        store = project_store(x_opsmineflow_project)
        return project_response(store, create_event_quality_report(store))

    @app.post("/automation/review")
    def automation_review(
        request: AutomationReviewRequest,
        x_opsmineflow_project: str = Header(default=""),
    ) -> dict[str, Any]:
        try:
            store = project_store(x_opsmineflow_project, expected_revision=request.expected_revision)
            return project_response(store, store.set_automation_review(request.activity, request.status, request.note))
        except ProjectConflictError as exc:
            raise _conflict(str(exc))
        except ValueError as exc:
            raise _bad_request(str(exc))

    @app.get("/analytics/process-map")
    def analytics_process_map(x_opsmineflow_project: str = Header(default="")) -> dict[str, Any]:
        store = project_store(x_opsmineflow_project)
        return project_response(store, create_process_map(store))

    @app.get("/reports/markdown")
    def report_markdown(x_opsmineflow_project: str = Header(default="")) -> dict[str, Any]:
        store = project_store(x_opsmineflow_project)
        return project_response(store, {"markdown": create_markdown_report(store)})

    @app.post("/export/mermaid")
    def export_mermaid_endpoint(x_opsmineflow_project: str = Header(default="")) -> dict[str, Any]:
        store = project_store(x_opsmineflow_project)
        return project_response(store, {"mermaid": str(create_export_artifact("mermaid", store)["content"])})

    @app.post("/export/drawio")
    def export_drawio_endpoint(x_opsmineflow_project: str = Header(default="")) -> dict[str, Any]:
        store = project_store(x_opsmineflow_project)
        return project_response(store, {"drawio": str(create_export_artifact("drawio", store)["content"])})

    @app.post("/export/svg")
    def export_svg_endpoint(x_opsmineflow_project: str = Header(default="")) -> dict[str, Any]:
        store = project_store(x_opsmineflow_project)
        return project_response(store, {"status": "planned", "message": "SVG export will use a local renderer."})

    @app.post("/export/csv")
    def export_csv_endpoint(x_opsmineflow_project: str = Header(default="")) -> dict[str, Any]:
        store = project_store(x_opsmineflow_project)
        artifact = create_export_artifact("csv", store)
        content = artifact["content"]
        if not isinstance(content, bytes):
            raise RuntimeError("CSV export must be a ZIP bundle.")
        return project_response(
            store,
            {"filename": artifact["filename"], "zip_base64": base64.b64encode(content).decode("ascii")},
        )

    @app.post("/export/json")
    def export_json_endpoint(x_opsmineflow_project: str = Header(default="")) -> dict[str, Any]:
        store = project_store(x_opsmineflow_project)
        return project_response(store, {"json": str(create_export_artifact("json", store)["content"])})

    @app.post("/export/llm-handoff")
    def export_llm_handoff_endpoint(x_opsmineflow_project: str = Header(default="")) -> dict[str, Any]:
        store = project_store(x_opsmineflow_project)
        return project_response(store, export_llm_handoff_payload(store))

    @app.post("/export/preview")
    def export_preview_endpoint(
        request: ExportPreviewRequest,
        x_opsmineflow_project: str = Header(default=""),
    ) -> dict[str, Any]:
        try:
            store = project_store(x_opsmineflow_project, expected_revision=request.expected_revision)
            artifact = create_export_artifact(request.format, store)
        except ValueError as exc:
            raise _bad_request(str(exc))
        return project_response(
            store,
            {key: artifact[key] for key in ("format", "filename", "byte_size", "preview", "confidential_count", "warning")},
        )

    @app.post("/export/save")
    def export_save_endpoint(
        request: ExportSaveRequest,
        x_opsmineflow_project: str = Header(default=""),
    ) -> dict[str, Any]:
        try:
            store = project_store(x_opsmineflow_project, expected_revision=request.expected_revision)
            return project_response(
                store,
                save_export_artifact(
                    request.format,
                    request.path,
                    overwrite_confirmed=request.overwrite_confirmed,
                    store=store,
                ),
            )
        except ValueError as exc:
            raise _bad_request(str(exc))
