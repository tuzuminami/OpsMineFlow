from __future__ import annotations

import csv
import hashlib
import json
import re
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .models import StandardEvent
from .privacy import IMPORTED_USER_ALIAS, extract_domain, looks_confidential, mask_url, mask_window_title

CSV_MAPPING_TARGETS = (
    "case_id",
    "activity",
    "timestamp_start",
    "timestamp_end",
    "duration_seconds",
    "app_name",
    "app_bundle_id",
    "domain",
    "source_event_id",
    "event_type",
)
MAX_EVENT_FIELD_BYTES = 64 * 1024
MAX_EVENT_METADATA_BYTES = 256 * 1024
CSV_COLUMN_SYNONYMS = {
    "case_id": ("case_id", "case", "case id", "案件", "案件id", "ケース", "ケースid"),
    "activity": ("activity", "activity_raw", "task", "work", "operation", "作業", "業務", "活動", "内容"),
    "timestamp_start": ("timestamp_start", "start", "started_at", "begin", "開始", "開始時刻", "開始時間"),
    "timestamp_end": ("timestamp_end", "end", "ended_at", "finish", "終了", "終了時刻", "終了時間"),
    "duration_seconds": ("duration_seconds", "duration", "seconds", "秒数", "滞在秒", "時間秒"),
    "user": ("user", "user_alias", "operator", "member", "担当者", "ユーザー", "利用者"),
    "app_name": ("app_name", "app", "application", "アプリ", "アプリ名", "利用アプリ"),
    "app_bundle_id": ("app_bundle_id", "bundle", "bundle_id", "bundle identifier"),
    "domain": ("domain", "host", "hostname", "url", "uri", "link", "リンク"),
    "window_title": ("window_title", "title", "window", "画面名", "ウィンドウ", "ウィンドウタイトル"),
    "url": ("url", "uri", "link", "リンク"),
    "memo": ("memo", "note", "notes", "description", "メモ", "備考", "説明"),
    "source_event_id": ("source_event_id", "id", "event_id", "イベントid", "ログid"),
    "event_type": ("event_type", "type", "種別"),
}

# These fields may be present in a customer-supplied CSV, but are never
# imported.  In particular, do not let a mapped import quietly turn a memo or
# window title into an activity label.  Users must provide an explicit
# activity column for process-mining labels.
SENSITIVE_CSV_MAPPING_TARGETS = frozenset({"user", "window_title", "url", "memo"})
SENSITIVE_CSV_COLUMN_NAMES = frozenset(
    "".join(character for character in synonym.casefold() if character.isalnum())
    for target in SENSITIVE_CSV_MAPPING_TARGETS
    for synonym in CSV_COLUMN_SYNONYMS[target]
)


def load_events_from_csv(
    path: str | Path,
    source: str = "csv",
    *,
    max_events: int | None = None,
) -> list[StandardEvent]:
    with Path(path).open("r", encoding="utf-8-sig", newline="") as handle:
        rows = _read_limited_csv_rows(csv.DictReader(handle), max_events)
    return [_event_from_csv_row(row, index, source) for index, row in enumerate(rows, start=1)]


def inspect_csv_columns(path: str | Path, sample_size: int = 5) -> dict[str, object]:
    with Path(path).open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        columns = list(reader.fieldnames or [])
        sample_rows = [
            {column: str(row.get(column) or "") for column in columns}
            for _, row in zip(range(sample_size), reader)
        ]
    return {"columns": columns, "sample_rows": sample_rows}


def suggest_csv_mapping(columns: Iterable[str]) -> dict[str, str]:
    column_list = list(columns)
    normalized_columns = {_normalize_column_name(column): column for column in column_list}
    used_columns: set[str] = set()
    mapping: dict[str, str] = {}
    for target in CSV_MAPPING_TARGETS:
        match = ""
        for synonym in CSV_COLUMN_SYNONYMS[target]:
            normalized_synonym = _normalize_column_name(synonym)
            if normalized_synonym in normalized_columns and normalized_columns[normalized_synonym] not in used_columns:
                match = normalized_columns[normalized_synonym]
                break
        if not match:
            for column in column_list:
                normalized_column = _normalize_column_name(column)
                if column in used_columns:
                    continue
                if any(_normalize_column_name(synonym) in normalized_column for synonym in CSV_COLUMN_SYNONYMS[target]):
                    match = column
                    break
        if match:
            mapping[target] = match
            used_columns.add(match)
    return mapping


def load_events_from_csv_with_mapping(
    path: str | Path,
    mapping: dict[str, str],
    *,
    date_format: str = "",
    timezone_name: str = "UTC",
    source: str = "csv_mapped",
    max_events: int | None = None,
) -> list[StandardEvent]:
    with Path(path).open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        rows = _read_limited_csv_rows(reader, max_events)
        columns = set(reader.fieldnames or [])
    cleaned_mapping = {
        target: column
        for target, column in mapping.items()
        if target in CSV_MAPPING_TARGETS and column in columns
    }
    # `url` was a public mapping key before #76.  Preserve the caller's
    # intent as a host-only domain mapping without accepting the raw URL.
    if "domain" not in cleaned_mapping and mapping.get("url") in columns:
        cleaned_mapping["domain"] = str(mapping["url"])
    sensitive_activity_column = cleaned_mapping.get("activity", "")
    if _normalize_column_name(sensitive_activity_column) in SENSITIVE_CSV_COLUMN_NAMES:
        raise ValueError("CSV mapping cannot use a memo, title, URL, or alias column as activity.")
    if "activity" not in cleaned_mapping:
        raise ValueError("CSV mapping requires an activity column.")
    if "timestamp_start" not in cleaned_mapping:
        raise ValueError("CSV mapping requires a start timestamp column.")
    return [
        _event_from_mapped_csv_row(row, index, cleaned_mapping, date_format, timezone_name, source)
        for index, row in enumerate(rows, start=1)
    ]


def load_events_from_json(
    path: str | Path,
    source: str = "json",
    *,
    max_events: int | None = None,
) -> list[StandardEvent]:
    with Path(path).open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if isinstance(payload, list):
        _ensure_event_limit(len(payload), max_events)
        return [_event_from_generic_json(item, index, source) for index, item in enumerate(payload, start=1)]
    if isinstance(payload, dict) and "buckets" in payload:
        events = list(_events_from_activitywatch_export(payload))
        _ensure_event_limit(len(events), max_events)
        return events
    if isinstance(payload, dict) and "events" in payload and isinstance(payload["events"], list):
        event_rows = payload["events"]
        _ensure_event_limit(len(event_rows), max_events)
        return [_event_from_generic_json(item, index, source) for index, item in enumerate(event_rows, start=1)]
    raise ValueError("Unsupported JSON event format")


def _read_limited_csv_rows(
    reader: csv.DictReader,
    max_events: int | None,
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for row in reader:
        if max_events is not None and len(rows) >= max_events:
            raise ValueError(f"Import is limited to {max_events:,} events per file.")
        rows.append(row)
    return rows


def _ensure_event_limit(event_count: int, max_events: int | None) -> None:
    if max_events is not None and event_count > max_events:
        raise ValueError(f"Import is limited to {max_events:,} events per file.")


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
    event = _build_event(
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
        case_source_provided=True,
    )
    metadata = json.loads(event.metadata_json)
    metadata["opsmineflow_case_correlation"] = {
        "origin": "manual",
        "strategy": "native_recording_case_label",
        "confidence": "medium",
        "evidence": "A local operator named this work unit before native recording started.",
    }
    return replace(event, metadata_json=json.dumps(metadata, ensure_ascii=False, sort_keys=True))


def _event_from_csv_row(row: dict[str, str], index: int, source: str) -> StandardEvent:
    start = _parse_datetime(row.get("timestamp_start") or row.get("start") or "")
    end_value = row.get("timestamp_end") or row.get("end") or ""
    end = _parse_datetime(end_value) if end_value else start
    duration = max((end - start).total_seconds(), 0.0)
    activity = row.get("activity") or row.get("activity_raw") or "Unlabeled activity"
    source_event_id = row.get("source_event_id") or str(index)
    source_case_id = row.get("case_id") or ""
    case_id = source_case_id or _fallback_case_id("", activity, index)
    return _build_event(
        source=source,
        source_event_id=source_event_id,
        case_id=case_id,
        user_alias=IMPORTED_USER_ALIAS,
        app_name=row.get("app_name") or "",
        app_bundle_id=row.get("app_bundle_id") or "",
        domain=extract_domain(row.get("url") or row.get("uri") or ""),
        window_title="",
        url="",
        activity_raw=activity,
        event_type=row.get("event_type") or "work_activity",
        timestamp_start=start,
        timestamp_end=end,
        duration_seconds=duration,
        idle_flag=_to_bool(row.get("idle_flag")),
        metadata={},
        case_source_provided=bool(source_case_id),
    )


def _event_from_mapped_csv_row(
    row: dict[str, str],
    index: int,
    mapping: dict[str, str],
    date_format: str,
    timezone_name: str,
    source: str,
) -> StandardEvent:
    def value(target: str) -> str:
        column = mapping.get(target, "")
        return str(row.get(column) or "").strip() if column else ""

    start = _parse_mapped_datetime(value("timestamp_start"), date_format, timezone_name)
    end_value = value("timestamp_end")
    duration_value = value("duration_seconds")
    duration = float(duration_value) if duration_value else 0.0
    end = _parse_mapped_datetime(end_value, date_format, timezone_name) if end_value else start + timedelta(seconds=duration)
    duration = max(float(duration_value) if duration_value else (end - start).total_seconds(), 0.0)
    activity = value("activity") or "Unlabeled activity"
    source_event_id = value("source_event_id") or str(index)
    source_case_id = value("case_id")
    return _build_event(
        source=source,
        source_event_id=source_event_id,
        case_id=source_case_id or _fallback_case_id("", activity, index),
        user_alias=IMPORTED_USER_ALIAS,
        app_name=value("app_name"),
        app_bundle_id=value("app_bundle_id"),
        domain=extract_domain(value("domain")),
        window_title="",
        url="",
        activity_raw=activity,
        event_type=value("event_type") or "work_activity",
        timestamp_start=start,
        timestamp_end=end,
        duration_seconds=duration,
        idle_flag=False,
        metadata={},
        case_source_provided=bool(source_case_id),
    )


def _event_from_generic_json(item: dict[str, Any], index: int, source: str) -> StandardEvent:
    start = _parse_datetime(str(item.get("timestamp_start") or item.get("start") or item.get("timestamp") or ""))
    duration_value = float(item.get("duration_seconds") or item.get("duration") or 0)
    end_raw = item.get("timestamp_end") or item.get("end")
    end = _parse_datetime(str(end_raw)) if end_raw else start + timedelta(seconds=duration_value)
    duration = max(float(item.get("duration_seconds") or (end - start).total_seconds()), 0.0)
    data = item.get("data") if isinstance(item.get("data"), dict) else {}
    explicit_activity = _optional_json_string(item.get("activity"), "activity")
    explicit_activity_raw = _optional_json_string(item.get("activity_raw"), "activity_raw")
    data_app = _optional_json_string(data.get("app"), "data.app")
    top_level_app = _optional_json_string(item.get("app_name"), "app_name")
    explicit_case_id = _optional_json_string(item.get("case_id"), "case_id").strip()
    raw_url = _optional_json_string(item.get("url"), "url") or _optional_json_string(data.get("url"), "data.url")
    activity = explicit_activity or explicit_activity_raw or "Unlabeled activity"
    return _build_event(
        source=source,
        source_event_id=str(item.get("source_event_id") or item.get("id") or index),
        case_id=explicit_case_id or _fallback_case_id("", activity, index),
        user_alias=IMPORTED_USER_ALIAS,
        app_name=top_level_app or data_app,
        app_bundle_id=str(item.get("app_bundle_id") or data.get("app_bundle_id") or ""),
        domain=extract_domain(raw_url),
        window_title="",
        url="",
        activity_raw=activity,
        event_type=str(item.get("event_type") or "work_activity"),
        timestamp_start=start,
        timestamp_end=end,
        duration_seconds=duration,
        idle_flag=bool(item.get("idle_flag") or data.get("status") == "afk"),
        metadata={},
        case_source_provided=bool(explicit_case_id),
    )


def _events_from_activitywatch_export(payload: dict[str, Any]) -> Iterable[StandardEvent]:
    index = 1
    buckets = payload.get("buckets") or {}
    for bucket_index, (_, bucket) in enumerate(buckets.items(), start=1):
        bucket_type = str(bucket.get("type") or "activitywatch")
        for item in bucket.get("events") or []:
            data = item.get("data") if isinstance(item.get("data"), dict) else {}
            start = _parse_datetime(str(item.get("timestamp") or ""))
            duration = float(item.get("duration") or 0)
            end = start + timedelta(seconds=duration)
            data_app = _optional_json_string(data.get("app"), "ActivityWatch data.app")
            data_browser = _optional_json_string(data.get("browser"), "ActivityWatch data.browser")
            app_name = data_app or data_browser
            activity = app_name or "Unlabeled activity"
            raw_url = _optional_json_string(data.get("url"), "ActivityWatch data.url")
            yield _build_event(
                source="activitywatch_export",
                source_event_id=f"{bucket_index}:{index}",
                case_id=_fallback_case_id("", activity, index),
                user_alias=IMPORTED_USER_ALIAS,
                app_name=app_name,
                app_bundle_id=_optional_json_string(data.get("app_bundle_id"), "ActivityWatch data.app_bundle_id"),
                domain=extract_domain(raw_url),
                window_title="",
                url="",
                activity_raw=activity,
                event_type=bucket_type,
                timestamp_start=start,
                timestamp_end=end,
                duration_seconds=duration,
                idle_flag=data.get("status") == "afk",
                metadata={},
                case_source_provided=False,
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
    domain: str = "",
    window_title: str,
    url: str,
    activity_raw: str,
    event_type: str,
    timestamp_start: datetime,
    timestamp_end: datetime,
    duration_seconds: float,
    idle_flag: bool,
    metadata: dict[str, Any],
    case_source_provided: bool | None = None,
) -> StandardEvent:
    _validate_event_text_sizes(
        {
            "source": source,
            "source event id": source_event_id,
            "case id": case_id,
            "user": user_alias,
            "app name": app_name,
            "app bundle id": app_bundle_id,
            "window title": window_title,
            "URL": url,
            "activity": activity_raw,
            "event type": event_type,
        }
    )
    normalized_metadata = {
        **metadata,
        "opsmineflow_case_correlation": _case_correlation_metadata(case_id, case_source_provided),
    }
    metadata_json = json.dumps(normalized_metadata, ensure_ascii=False, sort_keys=True)
    if len(metadata_json.encode("utf-8")) > MAX_EVENT_METADATA_BYTES:
        raise ValueError(
            f"Import event metadata exceeds the {MAX_EVENT_METADATA_BYTES // 1024} KiB safety limit."
        )
    timestamp_start_iso = _to_iso(timestamp_start)
    timestamp_end_iso = _to_iso(timestamp_end)
    created_at = _to_iso(datetime.now(timezone.utc))
    # Parsers keep source values only until EventStore applies its keyed,
    # project-scoped privacy boundary. Never retain URL authority components
    # as a "domain" fallback while that hand-off is in progress.
    safe_domain = extract_domain(domain or url)
    event_id = _stable_id(source, source_event_id, timestamp_start_iso, activity_raw)
    transient_case_id = case_id or event_id
    normalized = _normalize_activity(activity_raw)
    return StandardEvent(
        event_id=event_id,
        source=source,
        source_event_id=source_event_id,
        case_id=transient_case_id,
        session_id=f"{transient_case_id}:session-1",
        user_alias=user_alias,
        user_hash="",
        device_id="local-mac",
        app_name=app_name,
        app_bundle_id="",
        window_title=window_title,
        window_title_masked=mask_window_title(window_title),
        url=url,
        url_masked=mask_url(url),
        domain=safe_domain,
        activity_raw=activity_raw,
        activity_normalized=normalized,
        event_type=event_type,
        timestamp_start=timestamp_start_iso,
        timestamp_end=timestamp_end_iso,
        duration_seconds=duration_seconds,
        idle_flag=idle_flag,
        confidential_flag=looks_confidential(window_title, url, activity_raw),
        metadata_json=metadata_json,
        created_at=created_at,
    )


def _optional_json_string(value: object, field_name: str) -> str:
    if value is None or value == "":
        return ""
    if not isinstance(value, str):
        raise ValueError(f"JSON field {field_name} must be a string when provided.")
    return value


def _validate_event_text_sizes(values: dict[str, str]) -> None:
    for name, value in values.items():
        if len(value.encode("utf-8")) > MAX_EVENT_FIELD_BYTES:
            raise ValueError(f"Import event {name} exceeds the {MAX_EVENT_FIELD_BYTES // 1024} KiB safety limit.")


def _parse_datetime(value: str) -> datetime:
    if not value:
        raise ValueError("timestamp is required")
    normalized = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include an explicit UTC offset or use mapped import timezone")
    return parsed


def _parse_mapped_datetime(value: str, date_format: str, timezone_name: str) -> datetime:
    if not value:
        raise ValueError("timestamp is required")
    if date_format.strip():
        parsed = datetime.strptime(value, date_format.strip())
        if parsed.tzinfo is None:
            return _attach_import_timezone(parsed, timezone_name)
        return parsed
    try:
        return _parse_datetime(value)
    except ValueError:
        parsed = datetime.fromisoformat(value.replace("/", "-"))
        if parsed.tzinfo is None:
            return _attach_import_timezone(parsed, timezone_name)
        return parsed


def _to_iso(value: datetime) -> str:
    """Persist UTC instants; source/display timezones belong in provenance."""

    if value.tzinfo is None:
        raise ValueError("timestamp must include a timezone before storage")
    return value.astimezone(timezone.utc).isoformat()


def _to_bool(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "y"}


def _stable_id(*parts: str) -> str:
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
    return f"evt_{digest[:20]}"


def _normalize_activity(activity: str) -> str:
    return " ".join((activity or "unlabeled activity").strip().lower().split())


def _normalize_column_name(value: str) -> str:
    return "".join(character for character in value.casefold() if character.isalnum())


def _timezone_from_name(value: str) -> timezone | ZoneInfo:
    cleaned = value.strip() or "UTC"
    if cleaned.upper() in {"UTC", "Z"}:
        return timezone.utc
    if len(cleaned) == 6 and cleaned[0] in "+-" and cleaned[3] == ":":
        sign = 1 if cleaned[0] == "+" else -1
        hours = int(cleaned[1:3])
        minutes = int(cleaned[4:6])
        return timezone(sign * timedelta(hours=hours, minutes=minutes))
    try:
        return ZoneInfo(cleaned)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"Unknown timezone: {cleaned}") from exc


def _attach_import_timezone(value: datetime, timezone_name: str) -> datetime:
    """Attach a user-selected zone without silently resolving DST ambiguity."""

    zone = _timezone_from_name(timezone_name)
    if not isinstance(zone, ZoneInfo):
        return value.replace(tzinfo=zone)
    candidates: list[datetime] = []
    for fold in (0, 1):
        candidate = value.replace(tzinfo=zone, fold=fold)
        round_trip = candidate.astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None)
        if round_trip == value:
            candidates.append(candidate)
    if not candidates:
        raise ValueError(f"Local timestamp does not exist in {timezone_name}; provide an explicit UTC offset.")
    offsets = {candidate.utcoffset() for candidate in candidates}
    if len(offsets) > 1:
        raise ValueError(f"Local timestamp is ambiguous in {timezone_name}; provide an explicit UTC offset.")
    return candidates[0]


def _fallback_case_id(url: str, activity: str, index: int) -> str:
    # A domain or activity is not evidence that two events belong to the same
    # business instance. Keep unassigned events as reviewable singletons until
    # a user supplies or corrects a case identifier.
    del url, activity
    return f"CASE-UNASSIGNED-{index:08d}"


def _case_correlation_metadata(case_id: str, case_source_provided: bool | None) -> dict[str, str]:
    if case_source_provided is True:
        return {
            "origin": "observed",
            "strategy": "source_case_id",
            "confidence": "high",
            "evidence": "The source provided a case identifier.",
        }
    if case_source_provided is False or case_id.startswith("CASE-UNASSIGNED-"):
        return {
            "origin": "unassigned",
            "strategy": "singleton_without_source_case_id",
            "confidence": "low",
            "evidence": "The source did not provide a case identifier; this event is isolated until reviewed.",
        }
    if case_id.startswith(("CASE-INFERRED-", "CASE-DOMAIN-")):
        return {
            "origin": "inferred",
            "strategy": "legacy_fallback_case_id",
            "confidence": "low",
            "evidence": "A legacy fallback case identifier was supplied; it requires review before interpreting a flow.",
        }
    return {
        "origin": "observed",
        "strategy": "source_case_id",
        "confidence": "high",
        "evidence": "The source provided a case identifier.",
    }
