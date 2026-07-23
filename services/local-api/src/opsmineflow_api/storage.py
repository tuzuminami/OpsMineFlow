from __future__ import annotations

import hashlib
import json
import os
import secrets
import sqlite3
import uuid
from collections import OrderedDict
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, fields, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import RLock
from types import MappingProxyType
from typing import Any

from opsmineflow_mining import load_events_from_csv
from opsmineflow_mining.analysis import event_sort_key
from opsmineflow_mining.models import StandardEvent
from opsmineflow_mining.privacy import extract_domain, looks_confidential, mask_url, mask_window_title

from .migrations import (
    CURRENT_SCHEMA_VERSION,
    LEGACY_PROJECT_ID,
    MigrationReport,
    is_opaque_reference,
    load_verified_pseudonym_key,
    migrate_database,
    opaque_reference,
    redact_event_payload,
)


DEFAULT_SETTINGS: dict[str, object] = {
    "mask_url_paths": True,
    "mask_window_titles": True,
    "retention_days": 30,
    "session_gap_minutes": 30,
    "activitywatch_enabled": False,
    "excluded_apps": [],
    "excluded_domains": [],
}

MAX_CACHED_PROJECT_VIEWS = 2

AUTOMATION_REVIEW_STATUSES = {"unreviewed", "adopted", "on_hold", "rejected"}
_STANDARD_EVENT_FIELD_NAMES = tuple(field.name for field in fields(StandardEvent))
_IMPORT_FINGERPRINT_FIELD_NAMES = tuple(
    field_name for field_name in _STANDARD_EVENT_FIELD_NAMES if field_name not in {"created_at", "event_id"}
)


class StorageCommitError(RuntimeError):
    """A local mutation did not reach durable storage and was not applied."""

    def __init__(self, code: str = "storage_commit_failed") -> None:
        self.code = code
        details = {
            "storage_busy": ("Local storage could not be updated. No changes were applied.", True, "retry"),
            "storage_commit_failed": ("Local storage could not be updated. No changes were applied.", True, "retry"),
            "storage_unavailable": ("Local storage is unavailable. Check free space and permissions.", False, "check_local_storage"),
            "storage_read_only": ("Local storage is read-only. Check local storage permissions.", False, "check_permissions"),
            "storage_constraint_failed": ("Local storage rejected the change. No changes were applied.", False, "check_local_storage"),
            "storage_commit_indeterminate": (
                "Local storage was reconciled after an uncertain commit. Refresh the data before continuing.",
                False,
                "refresh_data",
            ),
            "storage_recovery_required": (
                "Local storage needs recovery before further changes can be made.",
                False,
                "recover_local_storage",
            ),
        }
        self.message, self.retryable, self.recovery_action = details.get(
            code,
            details["storage_commit_failed"],
        )
        super().__init__(self.message)

    def to_api_dict(self) -> dict[str, object]:
        return {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
            "recovery_action": self.recovery_action,
        }


class ProjectNotFoundError(KeyError):
    """The caller supplied an opaque project identifier that does not exist."""


class ProjectConflictError(ValueError):
    """A project mutation was based on an older project revision."""


@dataclass(frozen=True)
class Project:
    """Non-sensitive project catalogue data safe to expose to the desktop UI."""

    project_id: str
    display_name: str
    origin: str
    created_at: str
    updated_at: str
    revision: int
    event_count: int = 0

    def to_api_dict(self) -> dict[str, object]:
        return {
            "project_id": self.project_id,
            "display_name": self.display_name,
            "origin": self.origin,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "revision": self.revision,
            "event_count": self.event_count,
        }


@dataclass(frozen=True)
class StoreSnapshot:
    """One self-consistent local state observed by a reader or mutation."""

    events: tuple[StandardEvent, ...]
    manual_labels: Mapping[str, str]
    settings: Mapping[str, object]
    metadata: Mapping[str, str]
    import_history: tuple[Mapping[str, object], ...]
    automation_reviews: Mapping[str, str]
    automation_review_notes: Mapping[str, str]
    project_id: str
    project_revision: int
    generation: int


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
    settings: dict[str, object] = field(default_factory=lambda: _copy_settings(DEFAULT_SETTINGS))
    metadata: dict[str, str] = field(default_factory=dict)
    import_history: list[dict[str, object]] = field(default_factory=list)
    automation_reviews: dict[str, str] = field(default_factory=dict)
    automation_review_notes: dict[str, str] = field(default_factory=dict)
    db_path: Path | None = None
    project_id: str = ""
    expected_revision: int | None = None
    migration_fault_injector: Callable[[int], None] | None = field(default=None, repr=False, compare=False)
    mutation_fault_injector: Callable[[str], None] | None = field(default=None, repr=False, compare=False)
    _migration_ready: bool = field(default=False, repr=False, compare=False)
    _parent_migration_report: MigrationReport | None = field(default=None, repr=False, compare=False)
    _parent_pseudonym_key: bytes | None = field(default=None, repr=False, compare=False)
    _migration_report: MigrationReport | None = field(default=None, init=False, repr=False)
    _analysis_cache: dict[object, object] = field(default_factory=dict, init=False, repr=False, compare=False)
    _analysis_lock: RLock = field(default_factory=RLock, init=False, repr=False, compare=False)
    _mutation_lock: RLock = field(default_factory=RLock, init=False, repr=False, compare=False)
    _project_view_lock: RLock = field(default_factory=RLock, init=False, repr=False, compare=False)
    _project_views: OrderedDict[str, "EventStore"] = field(default_factory=OrderedDict, init=False, repr=False, compare=False)
    _generation: int = field(default=0, init=False, repr=False, compare=False)
    _project_revision: int = field(default=0, init=False, repr=False, compare=False)
    _writes_blocked: bool = field(default=False, init=False, repr=False, compare=False)
    _pseudonym_key: bytes = field(default_factory=lambda: secrets.token_bytes(32), init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        self.project_id = _normalize_project_id(self.project_id or LEGACY_PROJECT_ID)
        if self.db_path is None:
            self.events = _uniquify_event_ids(
                self._filter_events(
                    _minimize_events(
                        list(self.events),
                        pseudonym_key=self._pseudonym_key,
                        project_id=self.project_id,
                    )
                )
            )
            return
        self.db_path = Path(self.db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.db_path.parent, 0o700)
        if self._migration_ready:
            if self._parent_migration_report is None or self._parent_pseudonym_key is None:
                raise StorageCommitError("storage_recovery_required")
            self._migration_report = self._parent_migration_report
            self._pseudonym_key = self._parent_pseudonym_key
        else:
            self._migration_report = migrate_database(
                self.db_path,
                fault_injector=self.migration_fault_injector,
            )
            self._pseudonym_key = load_verified_pseudonym_key(self.db_path)
        if self.events:
            self.replace(self.events)
        else:
            self._load()

    def snapshot(self) -> StoreSnapshot:
        """Return one immutable view for a reader without exposing a torn mutation."""

        with self._mutation_lock:
            return self._snapshot_locked()

    def for_project(self, project_id: str, *, expected_revision: int | None = None) -> "EventStore":
        """Open an immutable project context; never use the mutable active pointer for data access."""

        normalized_project_id = _normalize_project_id(project_id)
        if self.db_path is None:
            if normalized_project_id != self.project_id:
                raise ProjectNotFoundError(normalized_project_id)
            if expected_revision is not None and expected_revision != self._project_revision:
                raise ProjectConflictError("Project changed. Refresh it before applying another change.")
            return self
        if expected_revision is None:
            # Dashboard reads arrive in parallel. Reuse one project-bound view
            # so a 100k-event project is deserialized once, not once per
            # endpoint. The view remains explicit: this cache never consults
            # or falls back to the workspace active-project preference.
            with self._project_view_lock:
                cached = self._project_views.get(normalized_project_id)
                if cached is not None:
                    self._project_views.move_to_end(normalized_project_id)
                    return cached
                cached = EventStore(
                    db_path=self.db_path,
                    project_id=normalized_project_id,
                    _migration_ready=True,
                    _parent_migration_report=self._migration_report,
                    _parent_pseudonym_key=self._pseudonym_key,
                )
                self._project_views[normalized_project_id] = cached
                while len(self._project_views) > MAX_CACHED_PROJECT_VIEWS:
                    self._project_views.popitem(last=False)
                return cached
        # A revision-bound operation must load and validate the durable state
        # itself. Drop any read view so the next unversioned dashboard refresh
        # cannot reuse a stale pre-mutation snapshot.
        self._invalidate_project_view(normalized_project_id)
        return EventStore(
            db_path=self.db_path,
            project_id=normalized_project_id,
            expected_revision=expected_revision,
            _migration_ready=True,
            _parent_migration_report=self._migration_report,
            _parent_pseudonym_key=self._pseudonym_key,
        )

    def list_projects(self) -> list[Project]:
        if self.db_path is None:
            return [
                Project(
                    project_id=self.project_id,
                    display_name="In-memory project",
                    origin="memory",
                    created_at="",
                    updated_at="",
                    revision=self._project_revision,
                    event_count=len(self.events),
                )
            ]
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT p.project_id, p.display_name, p.origin, p.created_at, p.updated_at, p.revision,
                       COUNT(e.event_id) AS event_count
                FROM projects AS p
                LEFT JOIN events AS e ON e.project_id = p.project_id
                GROUP BY p.project_id
                ORDER BY p.updated_at DESC, p.project_id ASC
                """
            ).fetchall()
        return [_project_from_row(row) for row in rows]

    def active_project_id(self) -> str:
        if self.db_path is None:
            return self.project_id
        with self._connect() as connection:
            row = connection.execute(
                "SELECT value FROM workspace_metadata WHERE key = 'active_project_id'"
            ).fetchone()
        if row is None:
            raise StorageCommitError("storage_constraint_failed")
        return _normalize_project_id(str(row[0]))

    def get_project(self, project_id: str | None = None) -> Project:
        normalized_project_id = _normalize_project_id(project_id or self.project_id)
        if self.db_path is None:
            if normalized_project_id != self.project_id:
                raise ProjectNotFoundError(normalized_project_id)
            return self.list_projects()[0]
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT p.project_id, p.display_name, p.origin, p.created_at, p.updated_at, p.revision,
                       COUNT(e.event_id) AS event_count
                FROM projects AS p
                LEFT JOIN events AS e ON e.project_id = p.project_id
                WHERE p.project_id = ?
                GROUP BY p.project_id
                """,
                (normalized_project_id,),
            ).fetchone()
        if row is None:
            raise ProjectNotFoundError(normalized_project_id)
        return _project_from_row(row)

    def create_project(self, display_name: str, *, origin: str = "user") -> Project:
        if self.db_path is None:
            raise StorageCommitError("storage_constraint_failed")
        name = _normalize_project_display_name(display_name)
        normalized_origin = origin.strip() or "user"
        now = datetime.now(timezone.utc).isoformat()
        project = Project(
            project_id=str(uuid.uuid4()),
            display_name=name,
            origin=normalized_origin,
            created_at=now,
            updated_at=now,
            revision=0,
            event_count=0,
        )
        with self._connect() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                _assert_project_name_available(connection, name)
                connection.execute(
                    """
                    INSERT INTO projects(project_id, display_name, origin, created_at, updated_at, revision)
                    VALUES(?, ?, ?, ?, ?, ?)
                    """,
                    (
                        project.project_id,
                        project.display_name,
                        project.origin,
                        project.created_at,
                        project.updated_at,
                        project.revision,
                    ),
                )
                connection.execute("COMMIT")
            except Exception:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise
        return project

    def select_project(self, project_id: str) -> Project:
        normalized_project_id = _normalize_project_id(project_id)
        if self.db_path is None:
            return self.get_project(normalized_project_id)
        with self._connect() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                project = _project_for_id(connection, normalized_project_id)
                connection.execute(
                    """
                    INSERT INTO workspace_metadata(key, value) VALUES('active_project_id', ?)
                    ON CONFLICT(key) DO UPDATE SET value = excluded.value
                    """,
                    (normalized_project_id,),
                )
                connection.execute("COMMIT")
            except Exception:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise
        return project

    def rename_project(self, project_id: str, display_name: str, *, expected_revision: int | None = None) -> Project:
        normalized_project_id = _normalize_project_id(project_id)
        name = _normalize_project_display_name(display_name)
        if self.db_path is None:
            if normalized_project_id != self.project_id:
                raise ProjectNotFoundError(normalized_project_id)
            return self.get_project()
        with self._connect() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                project = _project_for_id(connection, normalized_project_id)
                _assert_requested_project_revision(project, expected_revision)
                _assert_project_name_available(connection, name, excluding_project_id=normalized_project_id)
                updated_at = datetime.now(timezone.utc).isoformat()
                updated_revision = project.revision + 1
                connection.execute(
                    "UPDATE projects SET display_name = ?, updated_at = ?, revision = ? WHERE project_id = ?",
                    (name, updated_at, updated_revision, normalized_project_id),
                )
                connection.execute("COMMIT")
            except Exception:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise
        self._invalidate_project_view(normalized_project_id)
        return self.get_project(normalized_project_id)

    def delete_project(self, project_id: str, *, expected_revision: int | None = None) -> str:
        """Delete one project and only its scoped rows; keep at least one project available."""

        normalized_project_id = _normalize_project_id(project_id)
        if self.db_path is None:
            raise StorageCommitError("storage_constraint_failed")
        with self._connect() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                project = _project_for_id(connection, normalized_project_id)
                _assert_requested_project_revision(project, expected_revision)
                if project.event_count > 0:
                    raise ValueError("Clear the project's data before deleting the project.")
                remaining = connection.execute(
                    "SELECT project_id FROM projects WHERE project_id != ? ORDER BY updated_at DESC, project_id ASC LIMIT 1",
                    (normalized_project_id,),
                ).fetchone()
                if remaining is None:
                    raise ValueError("Create another project before deleting the last project.")
                replacement_project_id = str(remaining[0])
                active_project_id = connection.execute(
                    "SELECT value FROM workspace_metadata WHERE key = 'active_project_id'"
                ).fetchone()
                if active_project_id is not None and str(active_project_id[0]) == normalized_project_id:
                    connection.execute(
                        "UPDATE workspace_metadata SET value = ? WHERE key = 'active_project_id'",
                        (replacement_project_id,),
                    )
                for table_name in (
                    "manual_labels",
                    "automation_reviews",
                    "settings",
                    "metadata",
                    "import_history",
                    "events",
                ):
                    connection.execute(f"DELETE FROM {table_name} WHERE project_id = ?", (normalized_project_id,))
                connection.execute("DELETE FROM projects WHERE project_id = ?", (normalized_project_id,))
                connection.execute("COMMIT")
            except Exception:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise
        self._invalidate_project_view(normalized_project_id)
        return replacement_project_id

    def _snapshot_locked(self) -> StoreSnapshot:
        return StoreSnapshot(
            events=tuple(self.events),
            manual_labels=MappingProxyType(dict(self.manual_labels)),
            settings=MappingProxyType(_copy_settings(self.settings)),
            metadata=MappingProxyType(dict(self.metadata)),
            import_history=tuple(MappingProxyType(dict(item)) for item in self.import_history),
            automation_reviews=MappingProxyType(dict(self.automation_reviews)),
            automation_review_notes=MappingProxyType(dict(self.automation_review_notes)),
            project_id=self.project_id,
            project_revision=self._project_revision,
            generation=self._generation,
        )

    def _candidate(
        self,
        current: StoreSnapshot,
        *,
        events: list[StandardEvent] | tuple[StandardEvent, ...] | None = None,
        events_are_privacy_minimized: bool = False,
        trusted_references: frozenset[str] = frozenset(),
        manual_labels: dict[str, str] | None = None,
        settings: dict[str, object] | None = None,
        metadata: dict[str, str] | None = None,
        import_history: list[dict[str, object]] | tuple[dict[str, object], ...] | None = None,
        automation_reviews: dict[str, str] | None = None,
        automation_review_notes: dict[str, str] | None = None,
    ) -> StoreSnapshot:
        event_list = list(current.events if events is None else events)
        if events_are_privacy_minimized:
            candidate_events = tuple(sorted(event_list, key=event_sort_key))
        else:
            candidate_events = tuple(
                sorted(
                    _minimize_events(
                        event_list,
                        pseudonym_key=self._pseudonym_key,
                        project_id=self.project_id,
                        trusted_references=_event_references(current.events) | trusted_references,
                    ),
                    key=event_sort_key,
                )
            )
        candidate_labels = {
            self._event_reference(event_id): label
            for event_id, label in (current.manual_labels if manual_labels is None else manual_labels).items()
        }
        candidate_reviews = dict(current.automation_reviews if automation_reviews is None else automation_reviews)
        candidate_review_notes = {
            activity: note
            for activity, note in (current.automation_review_notes if automation_review_notes is None else automation_review_notes).items()
            if activity in candidate_reviews and note.strip()
        }
        live_event_ids = {event.event_id for event in candidate_events}
        return StoreSnapshot(
            events=candidate_events,
            manual_labels=MappingProxyType(
                {event_id: label for event_id, label in candidate_labels.items() if event_id in live_event_ids}
            ),
            settings=MappingProxyType(_copy_settings(current.settings if settings is None else settings)),
            metadata=MappingProxyType(dict(current.metadata if metadata is None else metadata)),
            import_history=tuple(
                MappingProxyType(
                    {
                        **dict(item),
                        "path": _safe_import_display_name(str(item.get("source") or ""), str(item.get("path") or "")),
                    }
                )
                for item in (current.import_history if import_history is None else import_history)
            ),
            automation_reviews=MappingProxyType(candidate_reviews),
            automation_review_notes=MappingProxyType(candidate_review_notes),
            project_id=current.project_id,
            project_revision=current.project_revision + 1,
            generation=current.generation + 1,
        )

    def _commit_candidate(self, candidate: StoreSnapshot) -> None:
        """Persist a full candidate state before making it observable in memory."""

        if self._writes_blocked:
            raise StorageCommitError("storage_recovery_required")
        if self.db_path is not None:
            connection: sqlite3.Connection | None = None
            persisted: StoreSnapshot | None = None
            committed = False
            transaction_started = False
            rollback_confirmed = False
            failure: Exception | None = None
            close_failure: Exception | None = None
            try:
                connection = self._connect()
                connection.execute("BEGIN IMMEDIATE")
                transaction_started = True
                self._assert_expected_project_revision(connection, candidate)
                persisted = self._write_candidate(connection, candidate)
                if self.mutation_fault_injector is not None:
                    self.mutation_fault_injector("before_commit")
                connection.execute("COMMIT")
                committed = True
                if self.mutation_fault_injector is not None:
                    self.mutation_fault_injector("after_commit")
            except Exception as error:
                failure = error
                if not committed:
                    rollback_confirmed = self._rollback_after_failed_commit(connection)
            finally:
                if connection is not None:
                    try:
                        connection.close()
                    except Exception as close_error:
                        close_failure = close_error
            if close_failure is not None:
                self._reconcile_after_connection_failure(close_failure)
            if failure is not None:
                if committed or (transaction_started and not rollback_confirmed):
                    self._reconcile_after_uncertain_commit(failure)
                if isinstance(failure, (StorageCommitError, ProjectNotFoundError, ProjectConflictError)):
                    raise failure
                raise StorageCommitError(_storage_error_code(failure)) from failure
            if persisted is None:
                raise StorageCommitError("storage_commit_failed")
            candidate = persisted
        self._apply_candidate(candidate)

    def _rollback_after_failed_commit(self, connection: sqlite3.Connection | None) -> bool:
        if connection is None:
            return False
        try:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
                return not connection.in_transaction
        except Exception:
            # Preserve the original durable-write failure. A failed rollback
            # must never leak a driver/path error to the UI.
            return False
        return False

    def _assert_expected_project_revision(self, connection: sqlite3.Connection, candidate: StoreSnapshot) -> None:
        row = connection.execute(
            "SELECT revision FROM projects WHERE project_id = ?",
            (candidate.project_id,),
        ).fetchone()
        if row is None:
            raise ProjectNotFoundError(candidate.project_id)
        durable_revision = int(row[0])
        if self.expected_revision is not None and durable_revision != self.expected_revision:
            raise ProjectConflictError("Project changed. Refresh it before applying another change.")
        if durable_revision != candidate.project_revision - 1:
            raise ProjectConflictError("Project changed. Refresh it before applying another change.")

    def _reconcile_after_uncertain_commit(self, error: Exception) -> None:
        """Converge memory with durable SQLite state after an uncertain response."""

        try:
            self._load()
        except Exception as reload_error:
            self._writes_blocked = True
            raise StorageCommitError("storage_recovery_required") from reload_error
        raise StorageCommitError("storage_commit_indeterminate") from error

    def _reconcile_after_connection_failure(self, error: Exception) -> None:
        """Reload what is readable, then fail closed when a connection leaked."""

        try:
            self._load()
        except Exception as reload_error:
            self._writes_blocked = True
            raise StorageCommitError("storage_recovery_required") from reload_error
        self._writes_blocked = True
        raise StorageCommitError("storage_recovery_required") from error

    def _write_candidate(self, connection: sqlite3.Connection, candidate: StoreSnapshot) -> StoreSnapshot:
        """Write every coupled table in the caller-owned transaction."""

        project_id = candidate.project_id
        connection.execute("DELETE FROM events WHERE project_id = ?", (project_id,))
        connection.executemany(
            "INSERT INTO events(project_id, event_id, payload_json) VALUES(?, ?, ?)",
            [
                (project_id, event.event_id, json.dumps(_declared_event_payload(event), ensure_ascii=False))
                for event in candidate.events
            ],
        )
        connection.execute("DELETE FROM manual_labels WHERE project_id = ?", (project_id,))
        connection.executemany(
            "INSERT INTO manual_labels(project_id, event_id, label) VALUES(?, ?, ?)",
            [(project_id, event_id, label) for event_id, label in sorted(candidate.manual_labels.items())],
        )
        connection.execute("DELETE FROM settings WHERE project_id = ?", (project_id,))
        connection.executemany(
            "INSERT INTO settings(project_id, key, value_json) VALUES(?, ?, ?)",
            [
                (project_id, key, json.dumps(value, ensure_ascii=False))
                for key, value in sorted(candidate.settings.items())
            ],
        )
        connection.execute("DELETE FROM metadata WHERE project_id = ?", (project_id,))
        connection.executemany(
            "INSERT INTO metadata(project_id, key, value) VALUES(?, ?, ?)",
            [(project_id, key, value) for key, value in sorted(candidate.metadata.items())],
        )
        connection.execute("DELETE FROM automation_reviews WHERE project_id = ?", (project_id,))
        connection.executemany(
            "INSERT INTO automation_reviews(project_id, activity, status, note, updated_at) VALUES(?, ?, ?, ?, ?)",
            [
                (
                    project_id,
                    activity,
                    status,
                    candidate.automation_review_notes.get(activity, ""),
                    datetime.now(timezone.utc).isoformat(),
                )
                for activity, status in sorted(candidate.automation_reviews.items())
            ],
        )
        connection.execute("DELETE FROM import_history WHERE project_id = ?", (project_id,))
        persisted_history: list[dict[str, object]] = []
        next_history_id = int(
            connection.execute(
                "SELECT COALESCE(MAX(id), 0) + 1 FROM import_history WHERE project_id = ?",
                (project_id,),
            ).fetchone()[0]
        )
        for item in candidate.import_history:
            persisted_item = dict(item)
            history_id = int(persisted_item.get("id", next_history_id))
            next_history_id = max(next_history_id, history_id + 1)
            connection.execute(
                "INSERT INTO import_history(project_id, id, source, path, event_count, imported_at) VALUES(?, ?, ?, ?, ?, ?)",
                (
                    project_id,
                    history_id,
                    persisted_item["source"],
                    persisted_item["path"],
                    persisted_item["event_count"],
                    persisted_item["imported_at"],
                ),
            )
            persisted_item["id"] = history_id
            persisted_history.append(persisted_item)
        updated_at = datetime.now(timezone.utc).isoformat()
        result = connection.execute(
            "UPDATE projects SET revision = ?, updated_at = ? WHERE project_id = ?",
            (candidate.project_revision, updated_at, project_id),
        )
        if result.rowcount != 1:
            raise ProjectNotFoundError(project_id)
        return replace(
            candidate,
            import_history=tuple(MappingProxyType(dict(item)) for item in persisted_history),
        )

    def _apply_candidate(self, candidate: StoreSnapshot) -> None:
        self.events = list(candidate.events)
        self.manual_labels = dict(candidate.manual_labels)
        self.settings = _copy_settings(candidate.settings)
        self.metadata = dict(candidate.metadata)
        self.import_history = [dict(item) for item in candidate.import_history]
        self.automation_reviews = dict(candidate.automation_reviews)
        self.automation_review_notes = dict(candidate.automation_review_notes)
        self._project_revision = candidate.project_revision
        self._generation = candidate.generation
        self._invalidate_analysis()

    def replace(self, events: list[StandardEvent], import_source: str = "", import_path: str = "") -> None:
        with self._mutation_lock:
            current = self._snapshot_locked()
            candidate_events = _uniquify_event_ids(
                _filter_events_for_settings(
                    _minimize_events(
                        list(events), pseudonym_key=self._pseudonym_key, project_id=self.project_id
                    ),
                    current.settings,
                )
            )
            metadata = dict(current.metadata)
            metadata["initialized"] = "true"
            history = [dict(item) for item in current.import_history]
            if import_source:
                import_key = _import_fingerprint(import_source, import_path, candidate_events)
                if (
                    metadata.get("last_import_fingerprint") == import_key
                    and _same_import_dataset(candidate_events, current.events)
                ):
                    return
                metadata["last_import_fingerprint"] = import_key
                history.append(_import_history_item(import_source, import_path, len(candidate_events)))
            self._commit_candidate(
                self._candidate(
                    current,
                    events=candidate_events,
                    events_are_privacy_minimized=_events_are_privacy_minimized(candidate_events),
                    trusted_references=_event_references(candidate_events),
                    manual_labels={},
                    metadata=metadata,
                    import_history=history,
                )
            )

    def append(
        self,
        events: list[StandardEvent],
        *,
        import_source: str = "",
        import_path: str = "",
    ) -> int:
        with self._mutation_lock:
            current = self._snapshot_locked()
            existing_ids = {event.event_id for event in current.events}
            filtered_events = _filter_events_for_settings(
                _minimize_events(list(events), pseudonym_key=self._pseudonym_key, project_id=self.project_id),
                current.settings,
            )
            candidates = [
                event
                for event in filtered_events
                if event.event_id not in existing_ids
            ]
            new_events = _uniquify_event_ids(candidates, reserved_ids=existing_ids)
            import_key = _import_fingerprint(import_source, import_path, filtered_events) if import_source else ""
            if import_key and current.metadata.get("last_import_fingerprint") == import_key:
                return 0
            if not new_events:
                if import_source:
                    history = [dict(item) for item in current.import_history]
                    history.append(_import_history_item(import_source, import_path, 0))
                    metadata = _initialized_metadata(current)
                    metadata["last_import_fingerprint"] = import_key
                    self._commit_candidate(self._candidate(current, import_history=history, metadata=metadata))
                return 0
            metadata = dict(current.metadata)
            metadata["initialized"] = "true"
            history = [dict(item) for item in current.import_history]
            if import_source:
                metadata["last_import_fingerprint"] = import_key
                history.append(_import_history_item(import_source, import_path, len(new_events)))
            candidate_events = [*current.events, *new_events]
            self._commit_candidate(
                self._candidate(
                    current,
                    events=candidate_events,
                    events_are_privacy_minimized=_events_are_privacy_minimized(candidate_events),
                    trusted_references=_event_references(new_events),
                    metadata=metadata,
                    import_history=history,
                )
            )
            return len(new_events)

    def set_label(self, event_id: str, label: str) -> None:
        event_id = self._event_reference(event_id)
        with self._mutation_lock:
            current = self._snapshot_locked()
            if not any(event.event_id == event_id for event in current.events):
                raise KeyError(event_id)
            labels = dict(current.manual_labels)
            labels[event_id] = label
            self._commit_candidate(self._candidate(current, manual_labels=labels))

    def update_event_activity(self, event_id: str, activity: str) -> dict[str, object]:
        event_id = self._event_reference(event_id)
        normalized_activity = activity.strip()
        if not normalized_activity:
            raise ValueError("Activity label is required.")
        with self._mutation_lock:
            current = self._snapshot_locked()
            index = _find_event_index(current.events, event_id)
            original = current.events[index]
            updated = _replace_event(
                original,
                activity_raw=normalized_activity,
                activity_normalized=_normalize_activity(normalized_activity),
                confidential_flag=looks_confidential(original.window_title, original.url, normalized_activity),
                metadata_json=_edited_metadata(original, "activity_update"),
            )
            events = list(current.events)
            events[index] = updated
            self._commit_candidate(self._candidate(current, events=events, metadata=_initialized_metadata(current)))
            return self.events[_find_event_index(tuple(self.events), event_id)].to_dict()

    def exclude_event(self, event_id: str) -> dict[str, object]:
        event_id = self._event_reference(event_id)
        with self._mutation_lock:
            current = self._snapshot_locked()
            index = _find_event_index(current.events, event_id)
            removed = current.events[index]
            labels = dict(current.manual_labels)
            labels.pop(event_id, None)
            self._commit_candidate(
                self._candidate(
                    current,
                    events=[*current.events[:index], *current.events[index + 1 :]],
                    manual_labels=labels,
                    metadata=_initialized_metadata(current),
                )
            )
            return {"excluded": True, "event_id": removed.event_id}

    def set_event_quality_review(self, event_id: str, status: str) -> dict[str, object]:
        event_id = self._event_reference(event_id)
        normalized_status = status.strip().casefold() or "approved"
        if normalized_status not in {"approved", "unreviewed"}:
            raise ValueError("Quality review status must be approved or unreviewed.")
        with self._mutation_lock:
            current = self._snapshot_locked()
            index = _find_event_index(current.events, event_id)
            original = current.events[index]
            updated = _replace_event(
                original,
                metadata_json=_edited_metadata(
                    original,
                    "quality_review",
                    quality_review_status=normalized_status,
                ),
            )
            events = list(current.events)
            events[index] = updated
            self._commit_candidate(self._candidate(current, events=events, metadata=_initialized_metadata(current)))
            return {"event_id": event_id, "quality_review_status": normalized_status}

    def update_event_case_correlation(self, event_id: str, case_id: str, reason: str) -> dict[str, object]:
        """Apply a local human case correction without inventing source evidence.

        A reviewer-supplied case ID is useful evidence, but it is distinct
        from a source-observed ID, so its provenance remains manual.
        """

        event_id = self._event_reference(event_id)
        normalized_case_id = case_id.strip()
        if not normalized_case_id:
            raise ValueError("Case identifier is required.")
        if len(normalized_case_id) > 256 or any(character in "\r\n\t" for character in normalized_case_id):
            raise ValueError("Case identifier must be a single line of at most 256 characters.")
        normalized_reason = reason.strip()
        if not normalized_reason:
            raise ValueError("A review reason is required for a manual case correction.")
        if len(normalized_reason) > 500 or any(character in "\r\n\t" for character in normalized_reason):
            raise ValueError("Review reason must be a single line of at most 500 characters.")
        with self._mutation_lock:
            current = self._snapshot_locked()
            index = _find_event_index(current.events, event_id)
            event = current.events[index]
            updated = _replace_event(
                event,
                case_id=normalized_case_id,
                session_id=f"{normalized_case_id}:manual-review",
                metadata_json=_case_correlation_metadata(event, "manual_case_correction", normalized_reason),
            )
            events = list(current.events)
            events[index] = updated
            self._commit_candidate(self._candidate(current, events=events, metadata=_initialized_metadata(current)))
            return self.events[_find_event_index(tuple(self.events), event_id)].to_dict()

    def split_event(
        self,
        event_id: str,
        split_after_seconds: float,
        first_activity: str = "",
        second_activity: str = "",
    ) -> dict[str, object]:
        event_id = self._event_reference(event_id)
        with self._mutation_lock:
            current = self._snapshot_locked()
            index = _find_event_index(current.events, event_id)
            event = current.events[index]
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
            labels = dict(current.manual_labels)
            labels.pop(event_id, None)
            self._commit_candidate(
                self._candidate(
                    current,
                    events=[*current.events[:index], first, second, *current.events[index + 1 :]],
                    manual_labels=labels,
                    metadata=_initialized_metadata(current),
                )
            )
            return {
                "split": True,
                "events": [
                    self.events[_find_event_index(tuple(self.events), self._event_reference(first.event_id))].to_dict(),
                    self.events[_find_event_index(tuple(self.events), self._event_reference(second.event_id))].to_dict(),
                ],
            }

    def merge_adjacent_events(self, first_event_id: str, second_event_id: str, activity: str = "") -> dict[str, object]:
        first_event_id = self._event_reference(first_event_id)
        second_event_id = self._event_reference(second_event_id)
        with self._mutation_lock:
            current = self._snapshot_locked()
            first_index = _find_event_index(current.events, first_event_id)
            second_index = _find_event_index(current.events, second_event_id)
            ordered = sorted(
                [(first_index, current.events[first_index]), (second_index, current.events[second_index])],
                key=lambda item: (item[1].timestamp_start, item[1].event_id),
            )
            left_index, left = ordered[0]
            right_index, right = ordered[1]
            timeline = sorted(enumerate(current.events), key=lambda item: (item[1].case_id, item[1].timestamp_start, item[1].event_id))
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
            events = [event for index, event in enumerate(current.events) if index not in {left_index, right_index}]
            events.append(merged)
            labels = dict(current.manual_labels)
            labels.pop(left.event_id, None)
            labels.pop(right.event_id, None)
            self._commit_candidate(
                self._candidate(current, events=events, manual_labels=labels, metadata=_initialized_metadata(current))
            )
            return {
                "merged": True,
                "event": self.events[
                    _find_event_index(tuple(self.events), self._event_reference(merged.event_id))
                ].to_dict(),
            }

    def clear(self) -> None:
        """Clear only this project's database rows.

        Future encrypted recovery artifacts remain workspace-level data. Their
        retention and deletion policy belongs to the dedicated backup and
        lifecycle work rather than a selected-project operation.
        """

        with self._mutation_lock:
            current = self._snapshot_locked()
            self._commit_candidate(
                self._candidate(
                    current,
                    events=[],
                    manual_labels={},
                    metadata={"initialized": "true"},
                    import_history=[],
                    automation_reviews={},
                    automation_review_notes={},
                )
            )

    def set_automation_review(self, activity: str, status: str, note: str = "") -> dict[str, str]:
        normalized_activity = activity.strip()
        normalized_status = status.strip().casefold()
        del note
        # Review notes are freeform and can contain customer or operator data.
        # Keep only the structured review status at rest; #44 can later add a
        # constrained rule/comment model if a product need remains.
        normalized_note = ""
        if not normalized_activity:
            raise ValueError("Automation activity is required.")
        if normalized_status not in AUTOMATION_REVIEW_STATUSES:
            raise ValueError("Review status must be unreviewed, adopted, on_hold, or rejected.")
        with self._mutation_lock:
            current = self._snapshot_locked()
            reviews = dict(current.automation_reviews)
            notes = dict(current.automation_review_notes)
            if normalized_status == "unreviewed" and not normalized_note:
                reviews.pop(normalized_activity, None)
                notes.pop(normalized_activity, None)
            else:
                reviews[normalized_activity] = normalized_status
                notes[normalized_activity] = normalized_note
            self._commit_candidate(
                self._candidate(current, automation_reviews=reviews, automation_review_notes=notes)
            )
            return {"activity": normalized_activity, "review_status": normalized_status, "review_note": normalized_note}

    def get_settings(self) -> dict[str, object]:
        return _copy_settings(self.snapshot().settings)

    def update_settings(self, updates: dict[str, object]) -> dict[str, object]:
        with self._mutation_lock:
            current = self._snapshot_locked()
            settings = _copy_settings(current.settings)
            for key, value in updates.items():
                if key in DEFAULT_SETTINGS:
                    settings[key] = _normalize_setting(key, value)
            events = _filter_events_for_settings(list(current.events), settings)
            live_event_ids = {event.event_id for event in events}
            labels = {event_id: label for event_id, label in current.manual_labels.items() if event_id in live_event_ids}
            self._commit_candidate(
                self._candidate(
                    current,
                    events=events,
                    manual_labels=labels,
                    settings=settings,
                    metadata=_initialized_metadata(current),
                )
            )
            return self.get_settings()

    def record_import(self, source: str, path: str, event_count: int, *, operation_id: str = "") -> None:
        with self._mutation_lock:
            current = self._snapshot_locked()
            metadata = _initialized_metadata(current)
            operation_marker = _record_import_operation_marker(operation_id) if operation_id else ""
            if operation_marker and metadata.get(operation_marker) == "committed":
                return
            history = [dict(item) for item in current.import_history]
            history.append(_import_history_item(source, path, event_count))
            if operation_marker:
                metadata[operation_marker] = "committed"
            self._commit_candidate(self._candidate(current, import_history=history, metadata=metadata))

    def list_import_history(self) -> list[dict[str, object]]:
        return [dict(item) for item in reversed(self.snapshot().import_history)]

    def is_initialized(self) -> bool:
        return self.snapshot().metadata.get("initialized") == "true"

    def filter_events(
        self,
        events: list[StandardEvent],
        snapshot: StoreSnapshot | None = None,
    ) -> list[StandardEvent]:
        active_snapshot = snapshot or self.snapshot()
        return _filter_events_for_settings(events, active_snapshot.settings)

    def _filter_events(self, events: list[StandardEvent]) -> list[StandardEvent]:
        return self.filter_events(events)

    def diagnostics(self, snapshot: StoreSnapshot | None = None) -> dict[str, object]:
        migration = self._migration_report
        active_snapshot = snapshot or self.snapshot()
        return {
            "storage_mode": "sqlite" if self.db_path else "memory",
            "storage_path": "",
            "project_id": _diagnostic_project_reference(active_snapshot.project_id),
            "project_revision": active_snapshot.project_revision,
            "event_count": len(active_snapshot.events),
            "manual_label_count": len(active_snapshot.manual_labels),
            "import_history_count": len(active_snapshot.import_history),
            "automation_review_count": len(active_snapshot.automation_reviews),
            "schema_version": migration.schema_version if migration else 0,
            "schema_target_version": CURRENT_SCHEMA_VERSION if self.db_path else 0,
            "migration_status": migration.status if migration else "not_applicable",
            "migration_backup_created": bool(migration and migration.backup_name),
            "integrity_status": migration.integrity_status if migration else "not_applicable",
            "wal_status": migration.wal_status if migration else "not_applicable",
            "backup_cleanup_status": migration.backup_cleanup_status if migration else "not_applicable",
        }

    def _event_reference(self, value: object) -> str:
        event_id = str(value or "").strip()
        if any(event.event_id == event_id for event in self.events):
            return event_id
        return opaque_reference(self._pseudonym_key, self.project_id, "evt", event_id)

    def event_reference_for_input(self, value: object) -> str:
        """Resolve a parser-bound or already-safe event ID for local comparisons."""

        return self._event_reference(value)

    def _connect(self) -> sqlite3.Connection:
        if self.db_path is None:
            raise RuntimeError("Persistent storage is not configured.")
        connection = sqlite3.connect(self.db_path, timeout=5, isolation_level=None)
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _invalidate_project_view(self, project_id: str) -> None:
        with self._project_view_lock:
            self._project_views.pop(project_id, None)

    def _load(self) -> None:
        with self._connect() as conn:
            project_row = conn.execute(
                "SELECT revision FROM projects WHERE project_id = ?",
                (self.project_id,),
            ).fetchone()
            if project_row is None:
                raise ProjectNotFoundError(self.project_id)
            if self.expected_revision is not None and int(project_row[0]) != self.expected_revision:
                raise ProjectConflictError("Project changed. Refresh it before applying another change.")
            event_rows = conn.execute(
                "SELECT payload_json FROM events WHERE project_id = ? ORDER BY rowid",
                (self.project_id,),
            ).fetchall()
            label_rows = conn.execute(
                "SELECT event_id, label FROM manual_labels WHERE project_id = ? ORDER BY event_id",
                (self.project_id,),
            ).fetchall()
            setting_rows = conn.execute(
                "SELECT key, value_json FROM settings WHERE project_id = ? ORDER BY key",
                (self.project_id,),
            ).fetchall()
            metadata_rows = conn.execute(
                "SELECT key, value FROM metadata WHERE project_id = ? ORDER BY key",
                (self.project_id,),
            ).fetchall()
            review_rows = conn.execute(
                "SELECT activity, status, note FROM automation_reviews WHERE project_id = ? ORDER BY activity",
                (self.project_id,),
            ).fetchall()
            import_rows = conn.execute(
                "SELECT id, source, path, event_count, imported_at FROM import_history WHERE project_id = ? ORDER BY id",
                (self.project_id,),
            ).fetchall()
        stored_events = [StandardEvent(**json.loads(row[0])) for row in event_rows]
        self.events = sorted(
            _minimize_events(
                stored_events,
                pseudonym_key=self._pseudonym_key,
                project_id=self.project_id,
                trusted_references=_event_references(stored_events),
            ),
            key=event_sort_key,
        )
        self.manual_labels = {str(event_id): str(label) for event_id, label in label_rows}
        self.settings = _copy_settings(DEFAULT_SETTINGS)
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
                "path": _safe_import_display_name(str(source), str(path)),
                "event_count": int(event_count),
                "imported_at": str(imported_at),
            }
            for row_id, source, path, event_count, imported_at in import_rows
        ]
        self._project_revision = int(project_row[0])
        self._generation += 1
        self._invalidate_analysis()

    def get_or_create_analysis(
        self,
        snapshot_generation: int,
        cache_key: object,
        builder: Callable[[], object],
    ) -> object:
        """Return one immutable analysis per local data/configuration snapshot.

        Dashboard routes arrive concurrently from the WebUI.  Preparing the
        same 100k-event receipt in every request both wastes CPU and can make
        otherwise bounded localhost requests time out.  Holding this small
        per-store lock means the first route prepares the result while the
        others reuse exactly that immutable result.  Every event/settings
        mutation clears the cache through ``_invalidate_analysis``.
        """

        with self._analysis_lock:
            generation_key = (snapshot_generation, cache_key)
            if generation_key not in self._analysis_cache:
                analysis = builder()
                if snapshot_generation == self._generation:
                    self._analysis_cache[generation_key] = analysis
                return analysis
            return self._analysis_cache[generation_key]

    def _invalidate_analysis(self) -> None:
        with self._analysis_lock:
            self._analysis_cache.clear()


def _safe_import_display_name(source: str, path_value: str) -> str:
    """Keep import history useful without retaining user-controlled filenames."""

    del path_value
    if source.startswith("activitywatch_local"):
        return "ActivityWatch (local)"
    if source.startswith("csv"):
        return "CSV import"
    if source.startswith("json"):
        return "JSON import"
    if source.startswith("native_"):
        return "Native recording"
    return "Imported data"


def _diagnostic_project_reference(project_id: str) -> str:
    digest = hashlib.sha256(f"opsmineflow:project:{project_id}".encode("utf-8")).hexdigest()
    return f"project_{digest[:16]}"


def _minimize_events(
    events: list[StandardEvent],
    *,
    pseudonym_key: bytes,
    project_id: str,
    trusted_references: frozenset[str] = frozenset(),
) -> list[StandardEvent]:
    """Use the v4 persistence allowlist for every write and in-memory store."""

    minimized: list[StandardEvent] = []
    for event in events:
        payload = redact_event_payload(
            _declared_event_payload(event),
            pseudonym_key=pseudonym_key,
            project_id=project_id,
            trusted_references=trusted_references,
        )
        minimized.append(StandardEvent(**payload))
    return minimized


def _declared_event_payload(event: StandardEvent) -> dict[str, object]:
    """Copy only declared event fields for the v4 persistence boundary.

    StandardEvent currently contains scalar values. Avoiding ``asdict()`` here
    removes a recursive deep-copy from large imports while preserving the
    exact field set consumed by ``redact_event_payload``. Dynamic/transient
    attributes are deliberately excluded before the privacy boundary runs.
    """

    return {field_name: getattr(event, field_name) for field_name in _STANDARD_EVENT_FIELD_NAMES}


def _event_references(events: list[StandardEvent] | tuple[StandardEvent, ...]) -> frozenset[str]:
    references: set[str] = set()
    for event in events:
        for value, kind in (
            (event.event_id, "evt"),
            (event.case_id, "case"),
            (event.source_event_id, "source"),
        ):
            if is_opaque_reference(value, kind):
                references.add(value)
    return frozenset(references)


def _events_are_privacy_minimized(events: list[StandardEvent] | tuple[StandardEvent, ...]) -> bool:
    """Recognize the immediate safe-minimization path used by large imports.

    ``_uniquify_event_ids`` may synthesize a non-v1 ID for a collision.  In
    that case the caller must re-run the privacy boundary so the derived ID is
    HMAC-pseudonymized before persistence.  Only fully opaque event, case,
    and source references may skip that otherwise redundant pass.
    """

    return all(
        is_opaque_reference(event.event_id, "evt")
        and is_opaque_reference(event.case_id, "case")
        and is_opaque_reference(event.source_event_id, "source")
        for event in events
    )


def _copy_settings(settings: Mapping[str, object]) -> dict[str, object]:
    copied: dict[str, object] = {}
    for key, value in settings.items():
        copied[key] = list(value) if isinstance(value, (list, tuple)) else value
    return copied


def _filter_events_for_settings(events: list[StandardEvent], settings: Mapping[str, object]) -> list[StandardEvent]:
    excluded_apps = {str(app).strip().casefold() for app in settings.get("excluded_apps", []) if str(app).strip()}
    excluded_domains = {
        str(domain).strip().casefold()
        for domain in settings.get("excluded_domains", [])
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


def _find_event_index(events: tuple[StandardEvent, ...], event_id: str) -> int:
    for index, event in enumerate(events):
        if event.event_id == event_id:
            return index
    raise KeyError(event_id)


def _initialized_metadata(snapshot: StoreSnapshot) -> dict[str, str]:
    metadata = dict(snapshot.metadata)
    metadata["initialized"] = "true"
    return metadata


def _import_history_item(source: str, path: str, event_count: int) -> dict[str, object]:
    return {
        "source": source,
        "path": _safe_import_display_name(source, path),
        "event_count": event_count,
        "imported_at": datetime.now(timezone.utc).isoformat(),
    }


def _record_import_operation_marker(operation_id: str) -> str:
    digest = hashlib.sha256(operation_id.encode("utf-8")).hexdigest()
    return f"recording_import:{digest}"


def _import_fingerprint(source: str, path: str, events: tuple[StandardEvent, ...] | list[StandardEvent]) -> str:
    """Recognize an immediate/restarted retry without retaining a raw local path."""

    payload = {
        "source": source,
        "path": _safe_import_display_name(source, path),
        "events": sorted(_canonical_event_fingerprint(event) for event in events),
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _same_import_dataset(
    left: tuple[StandardEvent, ...] | list[StandardEvent],
    right: tuple[StandardEvent, ...] | list[StandardEvent],
) -> bool:
    return sorted(_canonical_event_fingerprint(event) for event in left) == sorted(
        _canonical_event_fingerprint(event) for event in right
    )


def _canonical_event_fingerprint(event: StandardEvent) -> str:
    """Hash imported source content, excluding local import bookkeeping."""

    payload = {
        field_name: getattr(event, field_name)
        for field_name in _IMPORT_FINGERPRINT_FIELD_NAMES
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _storage_error_code(error: Exception) -> str:
    message = str(error).casefold()
    if "locked" in message or "busy" in message:
        return "storage_busy"
    if "read-only" in message or "readonly" in message:
        return "storage_read_only"
    if "full" in message or "disk i/o" in message or "unable to open" in message or isinstance(error, OSError):
        return "storage_unavailable"
    if "malformed" in message or "corrupt" in message:
        return "storage_recovery_required"
    if "constraint" in message:
        return "storage_constraint_failed"
    return "storage_commit_failed"


def _normalize_project_id(value: object) -> str:
    try:
        return str(uuid.UUID(str(value).strip()))
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError("Project ID must be a UUID.") from exc


def _normalize_project_display_name(value: object) -> str:
    name = str(value).strip()
    if not name or len(name) > 120 or any(ord(character) < 32 for character in name):
        raise ValueError("Project name must be 1-120 printable characters.")
    return name


def _project_from_row(row: tuple[object, ...]) -> Project:
    return Project(
        project_id=_normalize_project_id(row[0]),
        display_name=str(row[1]),
        origin=str(row[2]),
        created_at=str(row[3]),
        updated_at=str(row[4]),
        revision=int(row[5]),
        event_count=int(row[6]),
    )


def _project_for_id(connection: sqlite3.Connection, project_id: str) -> Project:
    row = connection.execute(
        """
        SELECT p.project_id, p.display_name, p.origin, p.created_at, p.updated_at, p.revision,
               COUNT(e.event_id) AS event_count
        FROM projects AS p
        LEFT JOIN events AS e ON e.project_id = p.project_id
        WHERE p.project_id = ?
        GROUP BY p.project_id
        """,
        (project_id,),
    ).fetchone()
    if row is None:
        raise ProjectNotFoundError(project_id)
    return _project_from_row(row)


def _assert_requested_project_revision(project: Project, expected_revision: int | None) -> None:
    if expected_revision is not None and project.revision != expected_revision:
        raise ProjectConflictError("Project changed. Refresh it before applying another change.")


def _assert_project_name_available(
    connection: sqlite3.Connection,
    display_name: str,
    *,
    excluding_project_id: str = "",
) -> None:
    rows = connection.execute("SELECT project_id, display_name FROM projects").fetchall()
    target = display_name.casefold()
    if any(str(name).casefold() == target and str(project_id) != excluding_project_id for project_id, name in rows):
        raise ValueError("A project with that name already exists.")


_STORE: EventStore | None = None


def default_store() -> EventStore:
    global _STORE
    if _STORE is None:
        db_path = default_data_dir() / "opsmineflow.sqlite3"
        _STORE = EventStore(db_path=db_path)
        snapshot = _STORE.snapshot()
        if not snapshot.events and snapshot.metadata.get("initialized") != "true":
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
    if key == "session_gap_minutes":
        try:
            number = int(value)
        except (TypeError, ValueError):
            return DEFAULT_SETTINGS[key]
        return min(max(number, 0), 24 * 60)
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
    if value.tzinfo is None:
        raise ValueError("timestamp must include a timezone before storage")
    return value.astimezone(timezone.utc).isoformat()


def _event_time_bounds(event: StandardEvent) -> tuple[datetime, datetime, float]:
    start = _parse_iso(event.timestamp_start)
    end = _parse_iso(event.timestamp_end)
    duration = max((end - start).total_seconds(), float(event.duration_seconds))
    if end <= start and duration > 0:
        end = start + timedelta(seconds=duration)
    return start, end, duration


def _normalize_activity(activity: str) -> str:
    return " ".join((activity or "unlabeled activity").strip().lower().split())


def _uniquify_event_ids(
    events: list[StandardEvent], *, reserved_ids: set[str] | None = None
) -> list[StandardEvent]:
    """Preserve every imported source row while keeping SQLite event IDs unique.

    Source identity is ``(source, source_event_id)``.  Exact re-imports and
    conflicts are deliberately retained here so ``prepare_analysis`` can count
    them in the analysis receipt rather than silently hiding the evidence at
    storage time.
    """

    # Imports normally arrive with already-pseudonymized, unique IDs.  Avoid
    # building collision groups and serializing every full event merely to
    # prove that common case.  The fallback preserves the canonical ordering
    # and deterministic collision handling when an ID is repeated or reserved.
    used = set(reserved_ids or set())
    unique_ids: set[str] = set()
    for event in events:
        if event.event_id in used or event.event_id in unique_ids:
            break
        unique_ids.add(event.event_id)
    else:
        return sorted(events, key=event_sort_key)

    grouped: dict[str, list[StandardEvent]] = {}
    for event in events:
        grouped.setdefault(event.event_id, []).append(event)
    result: list[StandardEvent] = []
    for original_id in sorted(grouped):
        group = sorted(grouped[original_id], key=_event_identity_sort_key)
        for occurrence, event in enumerate(group):
            event_id = original_id if original_id not in used else _derived_event_id(original_id, f"stored-{occurrence}")
            while event_id in used:
                occurrence += 1
                event_id = _derived_event_id(original_id, f"stored-{occurrence}")
            used.add(event_id)
            result.append(event if event_id == event.event_id else _replace_event(event, event_id=event_id))
    return sorted(result, key=event_sort_key)


def _event_identity_sort_key(event: StandardEvent) -> tuple[object, ...]:
    return (*event_sort_key(event), json.dumps(event.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":")))


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


def _case_correlation_metadata(event: StandardEvent, action: str, reason: str) -> str:
    try:
        metadata = json.loads(event.metadata_json) if event.metadata_json else {}
        if not isinstance(metadata, dict):
            metadata = {}
    except json.JSONDecodeError:
        metadata = {}
    metadata["opsmineflow_case_correlation"] = {
        "origin": "manual",
        "strategy": "local_reviewer_case_id",
        "confidence": "medium",
        "evidence": "A local reviewer supplied this case identifier.",
    }
    metadata["opsmineflow_case_correlation_review"] = {
        "action": action,
        "previous_case_id": event.case_id,
        "reason": reason,
        "operator": "local-reviewer",
        "changed_at": datetime.now(timezone.utc).isoformat(),
    }
    return json.dumps(metadata, ensure_ascii=False, sort_keys=True)
