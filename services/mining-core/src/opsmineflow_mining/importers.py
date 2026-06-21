from __future__ import annotations

import csv
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from .models import StandardEvent
from .privacy import extract_domain, looks_confidential, mask_url, mask_window_title


def load_events_from_csv(path: str | Path, source: str = "csv") -> list[StandardEvent]:
    with Path(path).open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    return [_event_from_csv_row(row, index, source) for index, row in enumerate(rows, start=1)]


def load_events_from_json(path: str | Path, source: str = "json") -> list[StandardEvent]:
    with Path(path).open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if isinstance(payload, list):
        return [_event_from_generic_json(item, index, source) for index, item in enumerate(payload, start=1)]
    if isinstance(payload, dict) and "buckets" in payload:
        return list(_events_from_activitywatch_export(payload))
    if isinstance(payload, dict) and "events" in payload and isinstance(payload["events"], list):
        return [_event_from_generic_json(item, index, source) for index, item in enumerate(payload["events"], start=1)]
    raise ValueError("Unsupported JSON event format")


def build_native_app_event(
    *,
    session_id: str,
    sequence: int,
    case_id: str,
    activity: str,
    app_name: str,
    app_bundle_id: str,
    timestamp_start: str,
    timestamp_end: str,
    duration_seconds: float,
) -> StandardEvent:
    start = _parse_datetime(timestamp_start)
    end = _parse_datetime(timestamp_end)
    measured_duration = max((end - start).total_seconds(), 0.0)
    duration = max(min(float(duration_seconds), measured_duration + 1.0), 0.0)
    return _build_event(
        source="native_mac_agent",
        source_event_id=f"{session_id}:{sequence}",
        case_id=case_id,
        user_alias="local-user",
        app_name=app_name,
        app_bundle_id=app_bundle_id,
        window_title="",
        url="",
        activity_raw=activity,
        event_type="native_app_activity",
        timestamp_start=start,
        timestamp_end=end,
        duration_seconds=duration,
        idle_flag=False,
        metadata={"session_id": session_id, "sequence": sequence, "capture_scope": "frontmost_app_only"},
    )


def _event_from_csv_row(row: dict[str, str], index: int, source: str) -> StandardEvent:
    start = _parse_datetime(row.get("timestamp_start") or row.get("start") or "")
    end_value = row.get("timestamp_end") or row.get("end") or ""
    end = _parse_datetime(end_value) if end_value else start
    duration = max((end - start).total_seconds(), 0.0)
    user_alias = row.get("user") or row.get("user_alias") or "unknown"
    activity = row.get("activity") or row.get("activity_raw") or row.get("memo") or "Unlabeled activity"
    url = row.get("url") or ""
    window_title = row.get("window_title") or row.get("memo") or activity
    source_event_id = row.get("source_event_id") or str(index)
    case_id = row.get("case_id") or _fallback_case_id(url, activity, index)
    return _build_event(
        source=source,
        source_event_id=source_event_id,
        case_id=case_id,
        user_alias=user_alias,
        app_name=row.get("app_name") or "",
        app_bundle_id=row.get("app_bundle_id") or "",
        window_title=window_title,
        url=url,
        activity_raw=activity,
        event_type=row.get("event_type") or "work_activity",
        timestamp_start=start,
        timestamp_end=end,
        duration_seconds=duration,
        idle_flag=_to_bool(row.get("idle_flag")),
        metadata={"memo": row.get("memo") or ""},
    )


def _event_from_generic_json(item: dict[str, Any], index: int, source: str) -> StandardEvent:
    start = _parse_datetime(str(item.get("timestamp_start") or item.get("start") or item.get("timestamp") or ""))
    duration_value = float(item.get("duration_seconds") or item.get("duration") or 0)
    end_raw = item.get("timestamp_end") or item.get("end")
    end = _parse_datetime(str(end_raw)) if end_raw else start + timedelta(seconds=duration_value)
    duration = max(float(item.get("duration_seconds") or (end - start).total_seconds()), 0.0)
    data = item.get("data") if isinstance(item.get("data"), dict) else {}
    url = str(item.get("url") or data.get("url") or "")
    activity = str(item.get("activity") or item.get("activity_raw") or data.get("title") or data.get("app") or "Unlabeled activity")
    return _build_event(
        source=source,
        source_event_id=str(item.get("source_event_id") or item.get("id") or index),
        case_id=str(item.get("case_id") or _fallback_case_id(url, activity, index)),
        user_alias=str(item.get("user") or item.get("user_alias") or "unknown"),
        app_name=str(item.get("app_name") or data.get("app") or ""),
        app_bundle_id=str(item.get("app_bundle_id") or data.get("app_bundle_id") or ""),
        window_title=str(item.get("window_title") or data.get("title") or activity),
        url=url,
        activity_raw=activity,
        event_type=str(item.get("event_type") or "work_activity"),
        timestamp_start=start,
        timestamp_end=end,
        duration_seconds=duration,
        idle_flag=bool(item.get("idle_flag") or data.get("status") == "afk"),
        metadata=item,
    )


def _events_from_activitywatch_export(payload: dict[str, Any]) -> Iterable[StandardEvent]:
    index = 1
    buckets = payload.get("buckets") or {}
    for bucket_id, bucket in buckets.items():
        bucket_type = str(bucket.get("type") or bucket_id)
        for item in bucket.get("events") or []:
            data = item.get("data") if isinstance(item.get("data"), dict) else {}
            start = _parse_datetime(str(item.get("timestamp") or ""))
            duration = float(item.get("duration") or 0)
            end = start + timedelta(seconds=duration)
            url = str(data.get("url") or "")
            app_name = str(data.get("app") or data.get("browser") or "")
            title = str(data.get("title") or data.get("url") or app_name or bucket_type)
            yield _build_event(
                source="activitywatch_export",
                source_event_id=f"{bucket_id}:{item.get('id') or index}",
                case_id=_fallback_case_id(url, title, index),
                user_alias="activitywatch_user",
                app_name=app_name,
                app_bundle_id=str(data.get("app_bundle_id") or ""),
                window_title=title,
                url=url,
                activity_raw=title,
                event_type=bucket_type,
                timestamp_start=start,
                timestamp_end=end,
                duration_seconds=duration,
                idle_flag=data.get("status") == "afk",
                metadata={"bucket_id": bucket_id, "event": item},
            )
            index += 1


def _build_event(
    *,
    source: str,
    source_event_id: str,
    case_id: str,
    user_alias: str,
    app_name: str,
    app_bundle_id: str,
    window_title: str,
    url: str,
    activity_raw: str,
    event_type: str,
    timestamp_start: datetime,
    timestamp_end: datetime,
    duration_seconds: float,
    idle_flag: bool,
    metadata: dict[str, Any],
) -> StandardEvent:
    timestamp_start_iso = _to_iso(timestamp_start)
    timestamp_end_iso = _to_iso(timestamp_end)
    created_at = _to_iso(datetime.now(timezone.utc))
    domain = extract_domain(url)
    event_id = _stable_id(source, source_event_id, timestamp_start_iso, activity_raw)
    normalized = _normalize_activity(activity_raw)
    return StandardEvent(
        event_id=event_id,
        source=source,
        source_event_id=source_event_id,
        case_id=case_id,
        session_id=f"{case_id}:session-1",
        user_alias=user_alias,
        user_hash=_hash_user(user_alias),
        device_id="local-mac",
        app_name=app_name,
        app_bundle_id=app_bundle_id,
        window_title=window_title,
        window_title_masked=mask_window_title(window_title),
        url=url,
        url_masked=mask_url(url),
        domain=domain,
        activity_raw=activity_raw,
        activity_normalized=normalized,
        event_type=event_type,
        timestamp_start=timestamp_start_iso,
        timestamp_end=timestamp_end_iso,
        duration_seconds=duration_seconds,
        idle_flag=idle_flag,
        confidential_flag=looks_confidential(window_title, url, activity_raw),
        metadata_json=json.dumps(metadata, ensure_ascii=False, sort_keys=True),
        created_at=created_at,
    )


def _parse_datetime(value: str) -> datetime:
    if not value:
        raise ValueError("timestamp is required")
    normalized = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _to_iso(value: datetime) -> str:
    return value.isoformat()


def _to_bool(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "y"}


def _hash_user(user_alias: str) -> str:
    digest = hashlib.sha256(f"opsmineflow:{user_alias}".encode("utf-8")).hexdigest()
    return f"user_{digest[:16]}"


def _stable_id(*parts: str) -> str:
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
    return f"evt_{digest[:20]}"


def _normalize_activity(activity: str) -> str:
    return " ".join((activity or "unlabeled activity").strip().lower().split())


def _fallback_case_id(url: str, activity: str, index: int) -> str:
    domain = extract_domain(url)
    if domain:
        return f"CASE-DOMAIN-{domain}"
    normalized = _normalize_activity(activity).replace(" ", "-")[:24]
    return f"CASE-INFERRED-{normalized or index}"
