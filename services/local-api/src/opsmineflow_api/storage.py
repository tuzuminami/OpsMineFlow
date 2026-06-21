from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from opsmineflow_mining import load_events_from_csv
from opsmineflow_mining.models import StandardEvent


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
        self.events.sort(key=lambda event: (event.timestamp_start, event.event_id))
        if self.db_path is not None:
            with self._connect() as conn:
                conn.executemany(
                    "INSERT OR IGNORE INTO events(event_id, payload_json) VALUES(?, ?)",
                    [(event.event_id, json.dumps(event.to_dict(), ensure_ascii=False)) for event in new_events],
                )
                conn.execute("INSERT OR REPLACE INTO metadata(key, value) VALUES(?, ?)", ("initialized", "true"))
        self.metadata["initialized"] = "true"
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

    def clear(self) -> None:
        self.events = []
        self.manual_labels = {}
        self.automation_reviews = {}
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

    def set_automation_review(self, activity: str, status: str) -> dict[str, str]:
        normalized_activity = activity.strip()
        normalized_status = status.strip().casefold()
        if not normalized_activity:
            raise ValueError("Automation activity is required.")
        if normalized_status not in AUTOMATION_REVIEW_STATUSES:
            raise ValueError("Review status must be unreviewed, adopted, on_hold, or rejected.")
        if normalized_status == "unreviewed":
            self.automation_reviews.pop(normalized_activity, None)
        else:
            self.automation_reviews[normalized_activity] = normalized_status
        if self.db_path is not None:
            with self._connect() as conn:
                if normalized_status == "unreviewed":
                    conn.execute("DELETE FROM automation_reviews WHERE activity = ?", (normalized_activity,))
                else:
                    conn.execute(
                        "INSERT OR REPLACE INTO automation_reviews(activity, status, updated_at) VALUES(?, ?, ?)",
                        (normalized_activity, normalized_status, datetime.now(timezone.utc).isoformat()),
                    )
        return {"activity": normalized_activity, "review_status": normalized_status}

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
                    updated_at TEXT NOT NULL
                )
                """
            )

    def _load(self) -> None:
        with self._connect() as conn:
            event_rows = conn.execute("SELECT payload_json FROM events ORDER BY rowid").fetchall()
            label_rows = conn.execute("SELECT event_id, label FROM manual_labels ORDER BY event_id").fetchall()
            setting_rows = conn.execute("SELECT key, value_json FROM settings ORDER BY key").fetchall()
            metadata_rows = conn.execute("SELECT key, value FROM metadata ORDER BY key").fetchall()
            review_rows = conn.execute("SELECT activity, status FROM automation_reviews ORDER BY activity").fetchall()
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
        self.automation_reviews = {str(activity): str(status) for activity, status in review_rows}
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
