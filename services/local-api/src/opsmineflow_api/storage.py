from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import replace
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from opsmineflow_mining import load_events_from_csv
from opsmineflow_mining.models import StandardEvent
from opsmineflow_mining.privacy import extract_domain, looks_confidential, mask_url, mask_window_title


DEFAULT_SETTINGS: dict[str, object] = {
    "mask_url_paths": True,
    "mask_window_titles": True,
    "retention_days": 30,
    "activitywatch_enabled": False,
    "excluded_apps": [],
    "excluded_domains": [],
}

AUTOMATION_REVIEW_STATUSES = {"unreviewed", "adopted", "on_hold", "rejected"}


def default_data_dir() -> Path:
    override = os.environ.get("OPSMINEFLOW_DATA_DIR")
    if override:
        return Path(override).expanduser()
    if os.name == "posix" and Path.home().joinpath("Library").exists():
        return Path.home() / "Library" / "Application Support" / "OpsMineFlow"
    return Path.home() / ".local" / "share" / "opsmineflow"


@dataclass
class EventStore:
    events: list[StandardEvent] = field(default_factory=list)
    manual_labels: dict[str, str] = field(default_factory=dict)
    settings: dict[str, object] = field(default_factory=lambda: dict(DEFAULT_SETTINGS))
    metadata: dict[str, str] = field(default_factory=dict)
    import_history: list[dict[str, object]] = field(default_factory=list)
    automation_reviews: dict[str, str] = field(default_factory=dict)
    automation_review_notes: dict[str, str] = field(default_factory=dict)
    db_path: Path | None = None

    def __post_init__(self) -> None:
        if self.db_path is None:
            return
        self.db_path = Path(self.db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()
        if self.events:
            self.replace(self.events)
        else:
            self._load()

    def replace(self, events: list[StandardEvent], import_source: str = "", import_path: str = "") -> None:
        self.events = self._filter_events(list(events))
        self.manual_labels = {}
        if self.db_path is None:
            if import_source:
                self.record_import(import_source, import_path, len(self.events))
            return
        with self._connect() as conn:
            conn.execute("DELETE FROM events")
            conn.execute("DELETE FROM manual_labels")
            conn.executemany(
                "INSERT INTO events(event_id, payload_json) VALUES(?, ?)",
                [(event.event_id, json.dumps(event.to_dict(), ensure_ascii=False)) for event in self.events],
            )
            conn.execute("INSERT OR REPLACE INTO metadata(key, value) VALUES(?, ?)", ("initialized", "true"))
        self.metadata["initialized"] = "true"
        if import_source:
            self.record_import(import_source, import_path, len(self.events))

    def append(self, events: list[StandardEvent]) -> int:
        existing_ids = {event.event_id for event in self.events}
        new_events = [event for event in self._filter_events(list(events)) if event.event_id not in existing_ids]
        if not new_events:
            return 0
        self.events.extend(new_events)
        self._persist_events()
        return len(new_events)

    def set_label(self, event_id: str, label: str) -> None:
        if not any(event.event_id == event_id for event in self.events):
            raise KeyError(event_id)
        self.manual_labels[event_id] = label
        if self.db_path is None:
            return
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO manual_labels(event_id, label) VALUES(?, ?)",
                (event_id, label),
            )

    def update_event_activity(self, event_id: str, activity: str) -> dict[str, object]:
        normalized_activity = activity.strip()
        if not normalized_activity:
            raise ValueError("Activity label is required.")
        index = self._find_event_index(event_id)
        self.events[index] = _replace_event(
            self.events[index],
            activity_raw=normalized_activity,
            activity_normalized=_normalize_activity(normalized_activity),
            confidential_flag=looks_confidential(
                self.events[index].window_title,
                self.events[index].url,
                normalized_activity,
            ),
            metadata_json=_edited_metadata(self.events[index], "activity_update"),
        )
        self._persist_events()
        return self.events[index].to_dict()

    def exclude_event(self, event_id: str) -> dict[str, object]:
        index = self._find_event_index(event_id)
        removed = self.events.pop(index)
        self.manual_labels.pop(event_id, None)
        self._persist_events()
        return {"excluded": True, "event_id": removed.event_id}

    def set_event_quality_review(self, event_id: str, status: str) -> dict[str, object]:
        normalized_status = status.strip().casefold() or "approved"
        if normalized_status not in {"approved", "unreviewed"}:
            raise ValueError("Quality review status must be approved or unreviewed.")
        index = self._find_event_index(event_id)
        self.events[index] = _replace_event(
            self.events[index],
            metadata_json=_edited_metadata(
                self.events[index],
                "quality_review",
                quality_review_status=normalized_status,
            ),
        )
        self._persist_events()
        return {"event_id": event_id, "quality_review_status": normalized_status}

    def split_event(
        self,
        event_id: str,
        split_after_seconds: float,
        first_activity: str = "",
        second_activity: str = "",
    ) -> dict[str, object]:
        index = self._find_event_index(event_id)
        event = self.events[index]
        start, end, duration = _event_time_bounds(event)
        split_after = float(split_after_seconds)
        if duration <= 1:
            raise ValueError("Event is too short to split.")
        if split_after <= 0 or split_after >= duration:
            raise ValueError("Split point must be inside the event duration.")

        split_at = start + timedelta(seconds=split_after)
        first_label = first_activity.strip() or event.activity_raw
        second_label = second_activity.strip() or event.activity_raw
        first = _replace_event(
            event,
            event_id=_derived_event_id(event.event_id, "split1"),
            source_event_id=f"{event.source_event_id}:split1",
            timestamp_start=_to_iso(start),
            timestamp_end=_to_iso(split_at),
            duration_seconds=split_after,
            activity_raw=first_label,
            activity_normalized=_normalize_activity(first_label),
            metadata_json=_edited_metadata(event, "split", part=1),
        )
        second = _replace_event(
            event,
            event_id=_derived_event_id(event.event_id, "split2"),
            source_event_id=f"{event.source_event_id}:split2",
            timestamp_start=_to_iso(split_at),
            timestamp_end=_to_iso(end),
            duration_seconds=max(duration - split_after, 0.0),
            activity_raw=second_label,
            activity_normalized=_normalize_activity(second_label),
            metadata_json=_edited_metadata(event, "split", part=2),
        )
        self.events[index : index + 1] = [first, second]
        self.manual_labels.pop(event_id, None)
        self._persist_events()
        return {"split": True, "events": [first.to_dict(), second.to_dict()]}

    def merge_adjacent_events(self, first_event_id: str, second_event_id: str, activity: str = "") -> dict[str, object]:
        first_index = self._find_event_index(first_event_id)
        second_index = self._find_event_index(second_event_id)
        ordered = sorted(
            [(first_index, self.events[first_index]), (second_index, self.events[second_index])],
            key=lambda item: (item[1].timestamp_start, item[1].event_id),
        )
        left_index, left = ordered[0]
        right_index, right = ordered[1]
        timeline = sorted(enumerate(self.events), key=lambda item: (item[1].case_id, item[1].timestamp_start, item[1].event_id))
        positions = {event.event_id: position for position, (_, event) in enumerate(timeline)}
        if left.case_id != right.case_id or abs(positions[left.event_id] - positions[right.event_id]) != 1:
            raise ValueError("Only adjacent events in the same case can be merged.")

        start = _parse_iso(left.timestamp_start)
        end = _parse_iso(right.timestamp_end)
        merged_activity = activity.strip() or (left.activity_raw if left.activity_raw == right.activity_raw else f"{left.activity_raw} + {right.activity_raw}")
        merged_app = left.app_name if left.app_name == right.app_name else f"{left.app_name or 'Unknown'} + {right.app_name or 'Unknown'}"
        merged_bundle = left.app_bundle_id if left.app_bundle_id == right.app_bundle_id else ""
        merged_url = left.url if left.url == right.url else ""
        merged_window = left.window_title if left.window_title == right.window_title else ""
        merged = _replace_event(
            left,
            event_id=_derived_event_id(left.event_id, f"merge-{right.event_id}"),
            source_event_id=f"{left.source_event_id}+{right.source_event_id}",
            app_name=merged_app,
            app_bundle_id=merged_bundle,
            window_title=merged_window,
            window_title_masked=mask_window_title(merged_window),
            url=merged_url,
            url_masked=mask_url(merged_url),
            domain=extract_domain(merged_url),
            activity_raw=merged_activity,
            activity_normalized=_normalize_activity(merged_activity),
            timestamp_start=_to_iso(start),
            timestamp_end=_to_iso(end),
            duration_seconds=max((end - start).total_seconds(), 0.0),
            confidential_flag=looks_confidential(merged_window, merged_url, merged_activity),
            metadata_json=_edited_metadata(left, "merge", merged_event_ids=[left.event_id, right.event_id]),
        )
        for remove_index in sorted([left_index, right_index], reverse=True):
            self.events.pop(remove_index)
        self.events.append(merged)
        self.manual_labels.pop(left.event_id, None)
        self.manual_labels.pop(right.event_id, None)
        self._persist_events()
        return {"merged": True, "event": merged.to_dict()}

    def clear(self) -> None:
        self.events = []
        self.manual_labels = {}
        self.automation_reviews = {}
        self.automation_review_notes = {}
        if self.db_path is None:
            self.import_history = []
            return
        with self._connect() as conn:
            conn.execute("DELETE FROM events")
            conn.execute("DELETE FROM manual_labels")
            conn.execute("DELETE FROM automation_reviews")
            conn.execute("DELETE FROM import_history")
            conn.execute("INSERT OR REPLACE INTO metadata(key, value) VALUES(?, ?)", ("initialized", "true"))
        self.import_history = []
        self.metadata["initialized"] = "true"

    def set_automation_review(self, activity: str, status: str, note: str = "") -> dict[str, str]:
        normalized_activity = activity.strip()
        normalized_status = status.strip().casefold()
        normalized_note = note.strip()
        if not normalized_activity:
            raise ValueError("Automation activity is required.")
        if normalized_status not in AUTOMATION_REVIEW_STATUSES:
            raise ValueError("Review status must be unreviewed, adopted, on_hold, or rejected.")
        if normalized_status == "unreviewed" and not normalized_note:
            self.automation_reviews.pop(normalized_activity, None)
            self.automation_review_notes.pop(normalized_activity, None)
        else:
            self.automation_reviews[normalized_activity] = normalized_status
            self.automation_review_notes[normalized_activity] = normalized_note
        if self.db_path is not None:
            with self._connect() as conn:
                if normalized_status == "unreviewed" and not normalized_note:
                    conn.execute("DELETE FROM automation_reviews WHERE activity = ?", (normalized_activity,))
                else:
                    conn.execute(
                        "INSERT OR REPLACE INTO automation_reviews(activity, status, note, updated_at) VALUES(?, ?, ?, ?)",
                        (normalized_activity, normalized_status, normalized_note, datetime.now(timezone.utc).isoformat()),
                    )
        return {"activity": normalized_activity, "review_status": normalized_status, "review_note": normalized_note}

    def get_settings(self) -> dict[str, object]:
        return dict(self.settings)

    def update_settings(self, updates: dict[str, object]) -> dict[str, object]:
        allowed = set(DEFAULT_SETTINGS)
        for key, value in updates.items():
            if key in allowed:
                self.settings[key] = _normalize_setting(key, value)
        self.events = self._filter_events(self.events)
        if self.db_path is not None:
            with self._connect() as conn:
                conn.execute("DELETE FROM events")
                conn.executemany(
                    "INSERT INTO events(event_id, payload_json) VALUES(?, ?)",
                    [(event.event_id, json.dumps(event.to_dict(), ensure_ascii=False)) for event in self.events],
                )
                conn.executemany(
                    "INSERT OR REPLACE INTO settings(key, value_json) VALUES(?, ?)",
                    [(key, json.dumps(value, ensure_ascii=False)) for key, value in self.settings.items()],
                )
        return self.get_settings()

    def record_import(self, source: str, path: str, event_count: int) -> None:
        imported_at = datetime.now(timezone.utc).isoformat()
        item: dict[str, object] = {
            "source": source,
            "path": path,
            "event_count": event_count,
            "imported_at": imported_at,
        }
        if self.db_path is None:
            self.import_history.append(item)
            return
        with self._connect() as conn:
            cursor = conn.execute(
                "INSERT INTO import_history(source, path, event_count, imported_at) VALUES(?, ?, ?, ?)",
                (source, path, event_count, imported_at),
            )
            item["id"] = cursor.lastrowid
        self.import_history.append(item)

    def list_import_history(self) -> list[dict[str, object]]:
        return list(reversed(self.import_history))

    def is_initialized(self) -> bool:
        return self.metadata.get("initialized") == "true"

    def _filter_events(self, events: list[StandardEvent]) -> list[StandardEvent]:
        excluded_apps = {str(app).strip().casefold() for app in self.settings.get("excluded_apps", []) if str(app).strip()}
        excluded_domains = {
            str(domain).strip().casefold()
            for domain in self.settings.get("excluded_domains", [])
            if str(domain).strip()
        }
        if not excluded_apps and not excluded_domains:
            return events
        filtered: list[StandardEvent] = []
        for event in events:
            app_name = event.app_name.casefold()
            domain = event.domain.casefold()
            if app_name in excluded_apps:
                continue
            if any(domain == excluded or domain.endswith(f".{excluded}") for excluded in excluded_domains):
                continue
            filtered.append(event)
        return filtered

    def _find_event_index(self, event_id: str) -> int:
        for index, event in enumerate(self.events):
            if event.event_id == event_id:
                return index
        raise KeyError(event_id)

    def _persist_events(self) -> None:
        self.events.sort(key=lambda event: (event.case_id, event.timestamp_start, event.event_id))
        live_event_ids = {event.event_id for event in self.events}
        self.manual_labels = {event_id: label for event_id, label in self.manual_labels.items() if event_id in live_event_ids}
        if self.db_path is not None:
            with self._connect() as conn:
                conn.execute("DELETE FROM events")
                conn.executemany(
                    "INSERT OR REPLACE INTO events(event_id, payload_json) VALUES(?, ?)",
                    [(event.event_id, json.dumps(event.to_dict(), ensure_ascii=False)) for event in self.events],
                )
                conn.execute(
                    f"DELETE FROM manual_labels WHERE event_id NOT IN ({','.join('?' for _ in live_event_ids)})"
                    if live_event_ids
                    else "DELETE FROM manual_labels",
                    tuple(live_event_ids),
                )
                conn.execute("INSERT OR REPLACE INTO metadata(key, value) VALUES(?, ?)", ("initialized", "true"))
        self.metadata["initialized"] = "true"

    def diagnostics(self) -> dict[str, object]:
        return {
            "storage_mode": "sqlite" if self.db_path else "memory",
            "storage_path": str(self.db_path) if self.db_path else "",
            "event_count": len(self.events),
            "manual_label_count": len(self.manual_labels),
            "import_history_count": len(self.import_history),
            "automation_review_count": len(self.automation_reviews),
        }

    def _connect(self) -> sqlite3.Connection:
        if self.db_path is None:
            raise RuntimeError("Persistent storage is not configured.")
        return sqlite3.connect(self.db_path)

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS events (
                    event_id TEXT PRIMARY KEY,
                    payload_json TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS manual_labels (
                    event_id TEXT PRIMARY KEY,
                    label TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value_json TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS import_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source TEXT NOT NULL,
                    path TEXT NOT NULL,
                    event_count INTEGER NOT NULL,
                    imported_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS automation_reviews (
                    activity TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    note TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL
                )
                """
            )
            columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(automation_reviews)").fetchall()}
            if "note" not in columns:
                conn.execute("ALTER TABLE automation_reviews ADD COLUMN note TEXT NOT NULL DEFAULT ''")

    def _load(self) -> None:
        with self._connect() as conn:
            event_rows = conn.execute("SELECT payload_json FROM events ORDER BY rowid").fetchall()
            label_rows = conn.execute("SELECT event_id, label FROM manual_labels ORDER BY event_id").fetchall()
            setting_rows = conn.execute("SELECT key, value_json FROM settings ORDER BY key").fetchall()
            metadata_rows = conn.execute("SELECT key, value FROM metadata ORDER BY key").fetchall()
            review_rows = conn.execute("SELECT activity, status, note FROM automation_reviews ORDER BY activity").fetchall()
            import_rows = conn.execute(
                "SELECT id, source, path, event_count, imported_at FROM import_history ORDER BY id"
            ).fetchall()
        self.events = [StandardEvent(**json.loads(row[0])) for row in event_rows]
        self.manual_labels = {str(event_id): str(label) for event_id, label in label_rows}
        self.settings = dict(DEFAULT_SETTINGS)
        for key, value_json in setting_rows:
            if key in DEFAULT_SETTINGS:
                self.settings[str(key)] = json.loads(value_json)
        self.metadata = {str(key): str(value) for key, value in metadata_rows}
        self.automation_reviews = {str(activity): str(status) for activity, status, _note in review_rows}
        self.automation_review_notes = {
            str(activity): str(note)
            for activity, _status, note in review_rows
            if str(note).strip()
        }
        self.import_history = [
            {
                "id": int(row_id),
                "source": str(source),
                "path": str(path),
                "event_count": int(event_count),
                "imported_at": str(imported_at),
            }
            for row_id, source, path, event_count, imported_at in import_rows
        ]


_STORE: EventStore | None = None


def default_store() -> EventStore:
    global _STORE
    if _STORE is None:
        db_path = default_data_dir() / "opsmineflow.sqlite3"
        _STORE = EventStore(db_path=db_path)
        if not _STORE.events and not _STORE.is_initialized():
            sample_path = Path(__file__).resolve().parents[4] / "data/sample/sample_events.csv"
            _STORE.replace(load_events_from_csv(sample_path))
    return _STORE


def _normalize_setting(key: str, value: object) -> object:
    if key in {"mask_url_paths", "mask_window_titles", "activitywatch_enabled"}:
        return bool(value)
    if key == "retention_days":
        try:
            number = int(value)
        except (TypeError, ValueError):
            return DEFAULT_SETTINGS[key]
        return min(max(number, 1), 365)
    if key in {"excluded_apps", "excluded_domains"}:
        if isinstance(value, str):
            items = value.replace("\n", ",").split(",")
        elif isinstance(value, list):
            items = [str(item) for item in value]
        else:
            items = []
        normalized: list[str] = []
        seen: set[str] = set()
        for item in items:
            cleaned = item.strip()
            key_value = cleaned.casefold()
            if cleaned and key_value not in seen:
                normalized.append(cleaned)
                seen.add(key_value)
        return normalized
    return value


def _replace_event(event: StandardEvent, **changes: object) -> StandardEvent:
    return replace(event, **changes)


def _parse_iso(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _to_iso(value: datetime) -> str:
    return value.isoformat()


def _event_time_bounds(event: StandardEvent) -> tuple[datetime, datetime, float]:
    start = _parse_iso(event.timestamp_start)
    end = _parse_iso(event.timestamp_end)
    duration = max((end - start).total_seconds(), float(event.duration_seconds))
    if end <= start and duration > 0:
        end = start + timedelta(seconds=duration)
    return start, end, duration


def _normalize_activity(activity: str) -> str:
    return " ".join((activity or "unlabeled activity").strip().lower().split())


def _derived_event_id(event_id: str, suffix: str) -> str:
    import hashlib

    digest = hashlib.sha256(f"{event_id}:{suffix}".encode("utf-8")).hexdigest()
    return f"evt_{digest[:20]}"


def _edited_metadata(event: StandardEvent, action: str, **extra: object) -> str:
    try:
        metadata = json.loads(event.metadata_json) if event.metadata_json else {}
        if not isinstance(metadata, dict):
            metadata = {"previous_metadata": metadata}
    except json.JSONDecodeError:
        metadata = {"previous_metadata_json": event.metadata_json}
    metadata.update(
        {
            "timeline_edit_action": action,
            "timeline_edit_source_event_id": event.event_id,
            "timeline_edited_at": datetime.now(timezone.utc).isoformat(),
            **extra,
        }
    )
    return json.dumps(metadata, ensure_ascii=False, sort_keys=True)
