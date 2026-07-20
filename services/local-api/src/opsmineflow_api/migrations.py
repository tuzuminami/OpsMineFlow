from __future__ import annotations

import hashlib
import hmac
import json
import re
import os
import secrets
import sqlite3
import tempfile
import threading
from contextlib import contextmanager
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import fcntl


CURRENT_SCHEMA_VERSION = 4
MAX_MIGRATION_BACKUPS = 3
PSEUDONYM_KEY_FILENAME = ".opsmineflow-pseudonym-v1.key"
PSEUDONYM_KEY_VERIFIER_METADATA_KEY = "privacy_pseudonym_key_verifier_v1"
PRIVACY_CLEANUP_METADATA_KEY = "privacy_v4_cleanup_complete"
_MIGRATION_LOCK = threading.RLock()
# The value is deliberately an opaque, stable UUID rather than a user-facing
# display name. A v3 migration creates this one project exactly once while it
# atomically moves the pre-project single dataset into the scoped tables.
LEGACY_PROJECT_ID = "b01eecad-1e18-5e88-bf34-8e8e8358cfcb"
LEGACY_PROJECT_DISPLAY_NAME = "Migrated data"
LEGACY_PROJECT_ORIGIN = "legacy_migration"
_KNOWN_LEGACY_TABLES = frozenset(
    {
        "events",
        "manual_labels",
        "settings",
        "metadata",
        "import_history",
        "automation_reviews",
    }
)
_KNOWN_LEGACY_TABLE_SETS = frozenset(
    {
        frozenset({"events", "manual_labels", "settings"}),
        frozenset({"events", "manual_labels", "settings", "metadata", "import_history"}),
        _KNOWN_LEGACY_TABLES,
    }
)
_LEGACY_TABLE_SIGNATURES: dict[str, tuple[tuple[str, str, int, str | None, int], ...]] = {
    "events": (("event_id", "TEXT", 0, None, 1), ("payload_json", "TEXT", 1, None, 0)),
    "manual_labels": (("event_id", "TEXT", 0, None, 1), ("label", "TEXT", 1, None, 0)),
    "settings": (("key", "TEXT", 0, None, 1), ("value_json", "TEXT", 1, None, 0)),
    "metadata": (("key", "TEXT", 0, None, 1), ("value", "TEXT", 1, None, 0)),
    "import_history": (
        ("id", "INTEGER", 0, None, 1),
        ("source", "TEXT", 1, None, 0),
        ("path", "TEXT", 1, None, 0),
        ("event_count", "INTEGER", 1, None, 0),
        ("imported_at", "TEXT", 1, None, 0),
    ),
    "automation_reviews": (
        ("activity", "TEXT", 0, None, 1),
        ("status", "TEXT", 1, None, 0),
        ("updated_at", "TEXT", 1, None, 0),
    ),
}
_LEGACY_AUTOMATION_REVIEW_WITH_NOTE = (
    ("activity", "TEXT", 0, None, 1),
    ("status", "TEXT", 1, None, 0),
    ("note", "TEXT", 1, "''", 0),
    ("updated_at", "TEXT", 1, None, 0),
)
_LEGACY_AUTOMATION_REVIEW_WITH_APPENDED_NOTE = (
    ("activity", "TEXT", 0, None, 1),
    ("status", "TEXT", 1, None, 0),
    ("updated_at", "TEXT", 1, None, 0),
    ("note", "TEXT", 1, "''", 0),
)
_SCHEMA_SIGNATURES: dict[
    int, dict[str, tuple[tuple[tuple[str, str, int, str | None, int], ...], ...]]
] = {
    1: {
        "schema_migrations": (
            (
                ("version", "INTEGER", 0, None, 1),
                ("name", "TEXT", 1, None, 0),
                ("checksum", "TEXT", 1, None, 0),
                ("applied_at", "TEXT", 1, None, 0),
            ),
        ),
        **{table_name: (signature,) for table_name, signature in _LEGACY_TABLE_SIGNATURES.items()},
        "automation_reviews": (
            _LEGACY_AUTOMATION_REVIEW_WITH_NOTE,
            _LEGACY_AUTOMATION_REVIEW_WITH_APPENDED_NOTE,
        ),
    }
}

# Schema version 2 changes data handling only: it removes historical absolute
# import paths. The table layouts intentionally remain identical to v1.
_SCHEMA_SIGNATURES[2] = _SCHEMA_SIGNATURES[1]

_SCHEMA_SIGNATURES[3] = {
    "schema_migrations": _SCHEMA_SIGNATURES[1]["schema_migrations"],
    "projects": (
        (
            ("project_id", "TEXT", 1, None, 1),
            ("display_name", "TEXT", 1, None, 0),
            ("origin", "TEXT", 1, None, 0),
            ("created_at", "TEXT", 1, None, 0),
            ("updated_at", "TEXT", 1, None, 0),
            ("revision", "INTEGER", 1, "0", 0),
        ),
    ),
    "workspace_metadata": (
        (
            ("key", "TEXT", 0, None, 1),
            ("value", "TEXT", 1, None, 0),
        ),
    ),
    "events": (
        (
            ("project_id", "TEXT", 1, None, 1),
            ("event_id", "TEXT", 1, None, 2),
            ("payload_json", "TEXT", 1, None, 0),
        ),
    ),
    "manual_labels": (
        (
            ("project_id", "TEXT", 1, None, 1),
            ("event_id", "TEXT", 1, None, 2),
            ("label", "TEXT", 1, None, 0),
        ),
    ),
    "settings": (
        (
            ("project_id", "TEXT", 1, None, 1),
            ("key", "TEXT", 1, None, 2),
            ("value_json", "TEXT", 1, None, 0),
        ),
    ),
    "metadata": (
        (
            ("project_id", "TEXT", 1, None, 1),
            ("key", "TEXT", 1, None, 2),
            ("value", "TEXT", 1, None, 0),
        ),
    ),
    "import_history": (
        (
            ("project_id", "TEXT", 1, None, 1),
            ("id", "INTEGER", 1, None, 2),
            ("source", "TEXT", 1, None, 0),
            ("path", "TEXT", 1, None, 0),
            ("event_count", "INTEGER", 1, None, 0),
            ("imported_at", "TEXT", 1, None, 0),
        ),
    ),
    "automation_reviews": (
        (
            ("project_id", "TEXT", 1, None, 1),
            ("activity", "TEXT", 1, None, 2),
            ("status", "TEXT", 1, None, 0),
            ("note", "TEXT", 1, "''", 0),
            ("updated_at", "TEXT", 1, None, 0),
        ),
    ),
}

# v4 changes the event payload contract without changing SQLite table layouts.
# Keeping the identical signature explicit makes the ledger reject a file that
# claims to have completed the privacy migration without its checked-in entry.
_SCHEMA_SIGNATURES[4] = _SCHEMA_SIGNATURES[3]


class MigrationError(RuntimeError):
    """A database cannot be safely opened or migrated."""


class UnsupportedSchemaError(MigrationError):
    """The database belongs to a newer OpsMineFlow release."""


class MigrationInvariantError(MigrationError):
    """The checked-in migration registry is malformed or was rewritten."""


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    checksum: str
    statements: tuple[str, ...]
    legacy_steps: tuple[str, ...] = ()

    def apply(self, connection: sqlite3.Connection, *, pseudonym_key: bytes | None = None) -> None:
        for statement in self.statements:
            connection.execute(statement)
        for step in self.legacy_steps:
            if step == "add_automation_review_note":
                columns = {
                    str(row[1])
                    for row in connection.execute("PRAGMA table_info(automation_reviews)").fetchall()
                }
                if "note" not in columns:
                    connection.execute("ALTER TABLE automation_reviews ADD COLUMN note TEXT NOT NULL DEFAULT ''")
                continue
            if step == "redact_import_history_paths":
                rows = connection.execute("SELECT id, source, path FROM import_history").fetchall()
                for row_id, source, path in rows:
                    connection.execute(
                        "UPDATE import_history SET path = ? WHERE id = ?",
                        (_safe_import_display_name(str(source), str(path)), int(row_id)),
                    )
                continue
            if step == "rebuild_as_project_scoped":
                _rebuild_as_project_scoped(connection)
                continue
            if step == "redact_event_payloads":
                if pseudonym_key is None:
                    raise MigrationInvariantError("Privacy migration requires a local pseudonym key.")
                _redact_event_payloads(connection, pseudonym_key=pseudonym_key)
                continue
            raise MigrationInvariantError(f"Unknown legacy migration step: {step}")


_MIGRATION_001_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS schema_migrations (
        version INTEGER PRIMARY KEY,
        name TEXT NOT NULL,
        checksum TEXT NOT NULL,
        applied_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS events (
        event_id TEXT PRIMARY KEY,
        payload_json TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS manual_labels (
        event_id TEXT PRIMARY KEY,
        label TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value_json TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS metadata (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS import_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source TEXT NOT NULL,
        path TEXT NOT NULL,
        event_count INTEGER NOT NULL,
        imported_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS automation_reviews (
        activity TEXT PRIMARY KEY,
        status TEXT NOT NULL,
        note TEXT NOT NULL DEFAULT '',
        updated_at TEXT NOT NULL
    )
    """,
)
_MIGRATION_001_LEGACY_STEPS = ("add_automation_review_note",)
_MIGRATION_001_CHECKSUM = "4791df32d17324b8769203b68e005319ca1c3cbf584bce4eb2a511690e1a17a2"

_MIGRATION_002_STATEMENTS: tuple[str, ...] = ()
_MIGRATION_002_LEGACY_STEPS = ("redact_import_history_paths",)
_MIGRATION_002_CHECKSUM = "77ecd83da344c9734128dfa62c8c85fd8c34652d2811d3f4b2d8bb5530dfdb17"

_MIGRATION_003_STATEMENTS: tuple[str, ...] = ()
_MIGRATION_003_LEGACY_STEPS = ("rebuild_as_project_scoped",)
_MIGRATION_003_CHECKSUM = "96a37ab2bf12768fb045f3c09b16a74a591e92a4456536aa6da64147b12e77cd"

_MIGRATION_004_STATEMENTS: tuple[str, ...] = ()
_MIGRATION_004_LEGACY_STEPS = ("redact_event_payloads",)
_MIGRATION_004_CHECKSUM = "5ffdaa06d57de72f49c4690ca35401e4142bad2bedd016df62e879796353945b"

MIGRATIONS: tuple[Migration, ...] = (
    Migration(
        version=1,
        name="baseline_event_store",
        checksum=_MIGRATION_001_CHECKSUM,
        statements=_MIGRATION_001_STATEMENTS,
        legacy_steps=_MIGRATION_001_LEGACY_STEPS,
    ),
    Migration(
        version=2,
        name="redact_import_history_paths",
        checksum=_MIGRATION_002_CHECKSUM,
        statements=_MIGRATION_002_STATEMENTS,
        legacy_steps=_MIGRATION_002_LEGACY_STEPS,
    ),
    Migration(
        version=3,
        name="scope_records_to_projects",
        checksum=_MIGRATION_003_CHECKSUM,
        statements=_MIGRATION_003_STATEMENTS,
        legacy_steps=_MIGRATION_003_LEGACY_STEPS,
    ),
    Migration(
        version=4,
        name="minimize_persisted_event_payloads",
        checksum=_MIGRATION_004_CHECKSUM,
        statements=_MIGRATION_004_STATEMENTS,
        legacy_steps=_MIGRATION_004_LEGACY_STEPS,
    ),
)


def _safe_import_display_name(source: str, path_value: str) -> str:
    """Return a non-sensitive label suitable for persistent import history."""

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


_SAFE_CORRELATION_ORIGINS = frozenset({"observed", "manual", "inferred", "unassigned"})
_SAFE_CORRELATION_CONFIDENCE = frozenset({"high", "medium", "low"})
_SAFE_QUALITY_STATUSES = frozenset({"approved", "unreviewed", "requires_correction"})
_SAFE_WINDOW_TITLE_ORIGINS = frozenset({"provided", "memo", "activity_fallback"})
_OPAQUE_REFERENCE_PATTERN = re.compile(r"^(?:case|source|evt)_v1_[0-9a-f]{32}$")


def redact_event_payload(
    payload: dict[str, object],
    *,
    pseudonym_key: bytes,
    project_id: str,
    trusted_references: frozenset[str] = frozenset(),
) -> dict[str, object]:
    """Drop unapproved raw capture values before an event reaches SQLite.

    This is deliberately a strict allowlist rather than a masking helper:
    previously unknown metadata must not become durable merely because a new
    importer supplied it.  Pseudonym-key management remains the separate #91
    concern; this boundary removes the original alias, title, URL and freeform
    metadata now.
    """

    raw_event_id = _required_payload_text(payload, "event_id")
    raw_case_id = str(payload.get("case_id") or "")
    raw_source_event_id = str(payload.get("source_event_id") or "")
    raw_activity = str(payload.get("activity_raw") or "")
    metadata = _safe_event_metadata(payload.get("metadata_json"))
    title_origin = str(metadata.get("opsmineflow_window_title_origin") or "")
    if title_origin in {"memo", "activity_fallback"}:
        raw_activity = "Unlabeled activity"
    event_id = (
        raw_event_id
        if _is_opaque_reference(raw_event_id, "evt") and raw_event_id in trusted_references
        else opaque_reference(pseudonym_key, project_id, "evt", raw_event_id)
    )
    safe_case_id = (
        raw_case_id
        if _is_opaque_reference(raw_case_id, "case") and raw_case_id in trusted_references
        else opaque_reference(pseudonym_key, project_id, "case", raw_case_id or raw_event_id)
    )
    safe_source_event_id = (
        raw_source_event_id
        if _is_opaque_reference(raw_source_event_id, "source") and raw_source_event_id in trusted_references
        else opaque_reference(pseudonym_key, project_id, "source", raw_source_event_id or raw_event_id)
    )
    safe_activity = _safe_activity_label(raw_activity, event_id)
    app_name = _bounded_text(payload.get("app_name"), fallback="Unknown application", maximum=120)
    source = _bounded_text(payload.get("source"), fallback="external_import", maximum=80)
    event_type = _bounded_text(payload.get("event_type"), fallback="work_activity", maximum=80)
    timestamp_start = _required_payload_text(payload, "timestamp_start")
    timestamp_end = _required_payload_text(payload, "timestamp_end")
    try:
        duration_seconds = max(float(payload.get("duration_seconds") or 0.0), 0.0)
    except (TypeError, ValueError) as exc:
        raise MigrationError("Event payload has an invalid duration and cannot be safely migrated.") from exc

    return {
        "event_id": event_id,
        "source": source,
        "source_event_id": safe_source_event_id,
        "case_id": safe_case_id,
        "session_id": f"{safe_case_id}:session-1",
        "user_alias": "",
        "user_hash": "",
        "device_id": "local-device",
        "app_name": app_name,
        "app_bundle_id": "",
        "window_title": "",
        "window_title_masked": "",
        "url": "",
        "url_masked": "",
        "domain": _safe_domain(payload.get("domain")),
        "activity_raw": safe_activity,
        "activity_normalized": " ".join(safe_activity.casefold().split()),
        "event_type": event_type,
        "timestamp_start": timestamp_start,
        "timestamp_end": timestamp_end,
        "duration_seconds": duration_seconds,
        "idle_flag": bool(payload.get("idle_flag")),
        "confidential_flag": bool(payload.get("confidential_flag")),
        "metadata_json": _canonical_json(metadata),
        "created_at": _required_payload_text(payload, "created_at"),
    }


def _redact_event_payloads(connection: sqlite3.Connection, *, pseudonym_key: bytes) -> None:
    """Atomically rewrite the current project-scoped payloads to v4 safe form."""

    rows = connection.execute("SELECT project_id, event_id, payload_json FROM events ORDER BY project_id, event_id").fetchall()
    replacements: list[tuple[str, str, str, str]] = []
    for project_id, event_id, encoded_payload in rows:
        try:
            decoded = json.loads(str(encoded_payload))
        except json.JSONDecodeError as exc:
            raise MigrationError("Event payload is not valid JSON; refusing an unsafe privacy migration.") from exc
        if not isinstance(decoded, dict) or str(decoded.get("event_id") or "") != str(event_id):
            raise MigrationError("Event payload identity is invalid; refusing an unsafe privacy migration.")
        redacted = redact_event_payload(
            {str(key): value for key, value in decoded.items()},
            pseudonym_key=pseudonym_key,
            project_id=str(project_id),
        )
        replacements.append((_canonical_json(redacted), str(project_id), str(event_id), str(redacted["event_id"])))
    _replace_event_primary_keys(connection, replacements)
    connection.execute("UPDATE automation_reviews SET note = ''")
    for project_id, row_id, source, path in connection.execute(
        "SELECT project_id, id, source, path FROM import_history"
    ).fetchall():
        connection.execute(
            "UPDATE import_history SET path = ? WHERE project_id = ? AND id = ?",
            (_safe_import_display_name(str(source), str(path)), str(project_id), int(row_id)),
        )
    _record_pseudonym_key_verifier(connection, pseudonym_key)


def _safe_event_metadata(value: object) -> dict[str, object]:
    try:
        decoded = json.loads(str(value or "{}"))
    except json.JSONDecodeError:
        decoded = {}
    if not isinstance(decoded, dict):
        return {}
    safe: dict[str, object] = {}
    correlation = decoded.get("opsmineflow_case_correlation")
    if isinstance(correlation, dict):
        origin = str(correlation.get("origin") or "unassigned")
        confidence = str(correlation.get("confidence") or "low")
        safe["opsmineflow_case_correlation"] = {
            "origin": origin if origin in _SAFE_CORRELATION_ORIGINS else "unassigned",
            "strategy": _safe_correlation_strategy(correlation.get("strategy")),
            "confidence": confidence if confidence in _SAFE_CORRELATION_CONFIDENCE else "low",
            "evidence": "Local correlation classification.",
        }
    quality_status = str(decoded.get("quality_review_status") or "")
    if quality_status in _SAFE_QUALITY_STATUSES:
        safe["quality_review_status"] = quality_status
    title_origin = str(decoded.get("opsmineflow_window_title_origin") or "")
    if title_origin in _SAFE_WINDOW_TITLE_ORIGINS:
        safe["opsmineflow_window_title_origin"] = title_origin
    capture_scope = str(decoded.get("capture_scope") or "")
    if capture_scope == "frontmost_app_only":
        safe["capture_scope"] = capture_scope
    return safe


def _safe_correlation_strategy(value: object) -> str:
    strategy = str(value or "")
    allowed = {
        "source_case_id",
        "native_recording_case_label",
        "singleton_without_source_case_id",
        "legacy_fallback_case_id",
        "local_reviewer_case_id",
    }
    return strategy if strategy in allowed else "unknown"


def _required_payload_text(payload: dict[str, object], key: str) -> str:
    value = str(payload.get(key) or "").strip()
    if not value:
        raise MigrationError("Event payload is missing a required field; refusing an unsafe privacy migration.")
    return value


def _bounded_text(value: object, *, fallback: str, maximum: int) -> str:
    text = str(value or "").strip()
    if not text:
        return fallback
    return text[:maximum]


def opaque_reference(pseudonym_key: bytes, project_id: str, kind: str, value: str) -> str:
    """Create a project-scoped, non-reversible local reference.

    The key never leaves the local application-support directory.  Including
    the project identifier in the HMAC input prevents the same raw ID from
    becoming a correlation key across locally separated datasets.
    """

    if kind not in {"case", "source", "evt"}:
        raise ValueError(f"Unsupported opaque reference kind: {kind}")
    material = f"opsmineflow:pseudonym:v1:{project_id}:{kind}:{value}".encode("utf-8")
    digest = hmac.new(pseudonym_key, material, hashlib.sha256).hexdigest()
    return f"{kind}_v1_{digest[:32]}"


def is_opaque_reference(value: str, kind: str) -> bool:
    return bool(_OPAQUE_REFERENCE_PATTERN.fullmatch(value) and value.startswith(f"{kind}_v1_"))


def _is_opaque_reference(value: str, kind: str) -> bool:
    """Compatibility alias for the internal payload sanitizer."""

    return is_opaque_reference(value, kind)


def _safe_domain(value: object) -> str:
    raw = _bounded_text(value, fallback="", maximum=4_096)
    if not raw:
        return ""
    try:
        parsed = urlparse(raw if "://" in raw else f"//{raw}")
        return (parsed.hostname or "").casefold()[:253]
    except ValueError:
        return ""


def load_or_create_pseudonym_key(data_dir: Path, *, allow_create: bool = True) -> bytes:
    """Load a local 0600 key or create it atomically without following links."""

    data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(data_dir, 0o700)
    path = data_dir / PSEUDONYM_KEY_FILENAME
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        metadata = None
    if metadata is not None:
        if not path.is_file() or path.is_symlink():
            raise MigrationError("The local pseudonym key must be a regular file.")
        key = path.read_bytes()
        if len(key) != 32:
            raise MigrationError("The local pseudonym key is invalid; refusing unsafe data access.")
        os.chmod(path, 0o600)
        return key
    if not allow_create:
        raise MigrationError("The local pseudonym key is missing; refusing unsafe data access.")
    key = secrets.token_bytes(32)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return load_or_create_pseudonym_key(data_dir, allow_create=allow_create)
    try:
        os.write(descriptor, key)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    _fsync_directory(data_dir)
    return key


def _pseudonym_key_verifier(pseudonym_key: bytes) -> str:
    return hmac.new(
        pseudonym_key,
        b"opsmineflow:pseudonym-key-verifier:v1",
        hashlib.sha256,
    ).hexdigest()


def _record_pseudonym_key_verifier(connection: sqlite3.Connection, pseudonym_key: bytes) -> None:
    connection.execute(
        """
        INSERT INTO workspace_metadata(key, value) VALUES(?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (PSEUDONYM_KEY_VERIFIER_METADATA_KEY, _pseudonym_key_verifier(pseudonym_key)),
    )


def _assert_pseudonym_key_matches(connection: sqlite3.Connection, pseudonym_key: bytes) -> None:
    row = connection.execute(
        "SELECT value FROM workspace_metadata WHERE key = ?",
        (PSEUDONYM_KEY_VERIFIER_METADATA_KEY,),
    ).fetchone()
    if row is None or not hmac.compare_digest(str(row[0]), _pseudonym_key_verifier(pseudonym_key)):
        raise MigrationError("The local pseudonym key does not match this database; refusing unsafe data access.")


def load_verified_pseudonym_key(db_path: Path) -> bytes:
    """Load one key and verify that exact value before a store can use it."""

    pseudonym_key = load_or_create_pseudonym_key(db_path.parent, allow_create=False)
    connection = _connect(db_path)
    try:
        _assert_pseudonym_key_matches(connection, pseudonym_key)
    finally:
        connection.close()
    return pseudonym_key


def _replace_event_primary_keys(
    connection: sqlite3.Connection,
    replacements: list[tuple[str, str, str, str]],
) -> None:
    """Rewrite event IDs and their label references inside one deferred FK transaction."""

    if len({(project_id, new_event_id) for _payload, project_id, _old_event_id, new_event_id in replacements}) != len(replacements):
        raise MigrationError("Pseudonymized event IDs collided; refusing unsafe privacy migration.")
    connection.execute("PRAGMA defer_foreign_keys = ON")
    temporary_rows: list[tuple[str, str, str, str]] = []
    for index, (payload, project_id, old_event_id, new_event_id) in enumerate(replacements):
        temporary_event_id = f"migration-v4-{index}-{secrets.token_hex(12)}"
        # The v3 foreign key does not cascade primary-key updates. Move the
        # child first while constraints are deferred, then move its parent to
        # the same temporary value. This keeps a one-to-one mapping even when
        # a later source ID would otherwise overlap a target ID.
        connection.execute(
            "UPDATE manual_labels SET event_id = ? WHERE project_id = ? AND event_id = ?",
            (temporary_event_id, project_id, old_event_id),
        )
        connection.execute(
            "UPDATE events SET event_id = ? WHERE project_id = ? AND event_id = ?",
            (temporary_event_id, project_id, old_event_id),
        )
        temporary_rows.append((payload, project_id, temporary_event_id, new_event_id))
    for payload, project_id, temporary_event_id, new_event_id in temporary_rows:
        connection.execute(
            "UPDATE manual_labels SET event_id = ? WHERE project_id = ? AND event_id = ?",
            (new_event_id, project_id, temporary_event_id),
        )
        connection.execute(
            "UPDATE events SET event_id = ?, payload_json = ? WHERE project_id = ? AND event_id = ?",
            (new_event_id, payload, project_id, temporary_event_id),
        )


def _safe_activity_label(value: str, event_id: str) -> str:
    normalized = " ".join(value.strip().split())
    if not normalized:
        return f"Activity {event_id[-6:]}"
    if len(normalized) > 160:
        return f"Activity {event_id[-6:]}"
    return normalized


def _rebuild_as_project_scoped(connection: sqlite3.Connection) -> None:
    """Move the v2 global dataset into one durable legacy project.

    SQLite cannot add composite primary keys or foreign keys with ``ALTER
    TABLE``. The v3 migration therefore builds fully constrained replacement
    tables, verifies a canonical before/after snapshot while still inside the
    startup transaction, then swaps the tables in one commit.
    """

    before_snapshot = _legacy_dataset_snapshot(connection)
    before_hash = _dataset_hash(before_snapshot)
    before_counts = _dataset_counts(before_snapshot)
    now = datetime.now(timezone.utc).isoformat()

    connection.execute(
        """
        CREATE TABLE projects (
            project_id TEXT NOT NULL PRIMARY KEY,
            display_name TEXT NOT NULL,
            origin TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            revision INTEGER NOT NULL DEFAULT 0 CHECK (revision >= 0)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE workspace_metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TRIGGER workspace_metadata_active_project_insert
        BEFORE INSERT ON workspace_metadata
        WHEN NEW.key = 'active_project_id'
          AND NOT EXISTS (SELECT 1 FROM projects WHERE project_id = NEW.value)
        BEGIN
            SELECT RAISE(ABORT, 'active project must exist');
        END
        """
    )
    connection.execute(
        """
        CREATE TRIGGER workspace_metadata_active_project_update
        BEFORE UPDATE OF key, value ON workspace_metadata
        WHEN NEW.key = 'active_project_id'
          AND NOT EXISTS (SELECT 1 FROM projects WHERE project_id = NEW.value)
        BEGIN
            SELECT RAISE(ABORT, 'active project must exist');
        END
        """
    )
    connection.execute(
        """
        CREATE TRIGGER projects_active_project_delete
        BEFORE DELETE ON projects
        WHEN EXISTS (
            SELECT 1 FROM workspace_metadata
            WHERE key = 'active_project_id' AND value = OLD.project_id
        )
        BEGIN
            SELECT RAISE(ABORT, 'active project cannot be deleted');
        END
        """
    )
    connection.execute(
        """
        CREATE TABLE events_v3 (
            project_id TEXT NOT NULL,
            event_id TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            PRIMARY KEY (project_id, event_id),
            FOREIGN KEY (project_id) REFERENCES projects(project_id) ON DELETE RESTRICT
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE manual_labels_v3 (
            project_id TEXT NOT NULL,
            event_id TEXT NOT NULL,
            label TEXT NOT NULL,
            PRIMARY KEY (project_id, event_id),
            FOREIGN KEY (project_id) REFERENCES projects(project_id) ON DELETE RESTRICT,
            FOREIGN KEY (project_id, event_id)
                REFERENCES events_v3(project_id, event_id) ON DELETE CASCADE
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE settings_v3 (
            project_id TEXT NOT NULL,
            key TEXT NOT NULL,
            value_json TEXT NOT NULL,
            PRIMARY KEY (project_id, key),
            FOREIGN KEY (project_id) REFERENCES projects(project_id) ON DELETE RESTRICT
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE metadata_v3 (
            project_id TEXT NOT NULL,
            key TEXT NOT NULL,
            value TEXT NOT NULL,
            PRIMARY KEY (project_id, key),
            FOREIGN KEY (project_id) REFERENCES projects(project_id) ON DELETE RESTRICT
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE import_history_v3 (
            project_id TEXT NOT NULL,
            id INTEGER NOT NULL,
            source TEXT NOT NULL,
            path TEXT NOT NULL,
            event_count INTEGER NOT NULL,
            imported_at TEXT NOT NULL,
            PRIMARY KEY (project_id, id),
            FOREIGN KEY (project_id) REFERENCES projects(project_id) ON DELETE RESTRICT
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE automation_reviews_v3 (
            project_id TEXT NOT NULL,
            activity TEXT NOT NULL,
            status TEXT NOT NULL,
            note TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL,
            PRIMARY KEY (project_id, activity),
            FOREIGN KEY (project_id) REFERENCES projects(project_id) ON DELETE RESTRICT
        )
        """
    )

    connection.execute(
        """
        INSERT INTO projects(project_id, display_name, origin, created_at, updated_at, revision)
        VALUES(?, ?, ?, ?, ?, 0)
        """,
        (LEGACY_PROJECT_ID, LEGACY_PROJECT_DISPLAY_NAME, LEGACY_PROJECT_ORIGIN, now, now),
    )
    connection.execute(
        "INSERT INTO events_v3(project_id, event_id, payload_json) "
        "SELECT ?, event_id, payload_json FROM events",
        (LEGACY_PROJECT_ID,),
    )
    connection.execute(
        "INSERT INTO manual_labels_v3(project_id, event_id, label) "
        "SELECT ?, event_id, label FROM manual_labels",
        (LEGACY_PROJECT_ID,),
    )
    connection.execute(
        "INSERT INTO settings_v3(project_id, key, value_json) "
        "SELECT ?, key, value_json FROM settings",
        (LEGACY_PROJECT_ID,),
    )
    connection.execute(
        "INSERT INTO metadata_v3(project_id, key, value) "
        "SELECT ?, key, value FROM metadata",
        (LEGACY_PROJECT_ID,),
    )
    connection.execute(
        "INSERT INTO import_history_v3(project_id, id, source, path, event_count, imported_at) "
        "SELECT ?, id, source, path, event_count, imported_at FROM import_history",
        (LEGACY_PROJECT_ID,),
    )
    connection.execute(
        "INSERT INTO automation_reviews_v3(project_id, activity, status, note, updated_at) "
        "SELECT ?, activity, status, note, updated_at FROM automation_reviews",
        (LEGACY_PROJECT_ID,),
    )

    after_snapshot = _project_scoped_dataset_snapshot(connection, LEGACY_PROJECT_ID, table_suffix="_v3")
    after_hash = _dataset_hash(after_snapshot)
    after_counts = _dataset_counts(after_snapshot)
    if after_hash != before_hash or after_counts != before_counts:
        raise MigrationError("Project migration verification failed; the legacy dataset was not copied exactly.")

    audit_values = (
        ("legacy_v2_before_hash", before_hash),
        ("legacy_v2_before_counts", _canonical_json(before_counts)),
        ("legacy_v3_after_hash", after_hash),
        ("legacy_v3_after_counts", _canonical_json(after_counts)),
        ("active_project_id", LEGACY_PROJECT_ID),
    )
    connection.executemany("INSERT INTO workspace_metadata(key, value) VALUES(?, ?)", audit_values)

    # Drop the unscoped tables only after every row has been copied and
    # verified. The transaction in ``migrate_database`` makes this swap atomic.
    for table_name in (
        "manual_labels",
        "automation_reviews",
        "settings",
        "metadata",
        "import_history",
        "events",
    ):
        connection.execute(f"DROP TABLE {table_name}")
    for old_name, new_name in (
        ("events_v3", "events"),
        ("manual_labels_v3", "manual_labels"),
        ("settings_v3", "settings"),
        ("metadata_v3", "metadata"),
        ("import_history_v3", "import_history"),
        ("automation_reviews_v3", "automation_reviews"),
    ):
        connection.execute(f"ALTER TABLE {old_name} RENAME TO {new_name}")


def _legacy_dataset_snapshot(connection: sqlite3.Connection) -> dict[str, list[list[object]]]:
    return {
        "events": _fetch_rows(connection, "SELECT event_id, payload_json FROM events ORDER BY event_id"),
        "manual_labels": _fetch_rows(connection, "SELECT event_id, label FROM manual_labels ORDER BY event_id"),
        "settings": _fetch_rows(connection, "SELECT key, value_json FROM settings ORDER BY key"),
        "metadata": _fetch_rows(connection, "SELECT key, value FROM metadata ORDER BY key"),
        "import_history": _fetch_rows(
            connection,
            "SELECT id, source, path, event_count, imported_at FROM import_history ORDER BY id",
        ),
        "automation_reviews": _fetch_rows(
            connection,
            "SELECT activity, status, note, updated_at FROM automation_reviews ORDER BY activity",
        ),
    }


def _project_scoped_dataset_snapshot(
    connection: sqlite3.Connection,
    project_id: str,
    *,
    table_suffix: str = "",
) -> dict[str, list[list[object]]]:
    return {
        "events": _fetch_rows(
            connection,
            f"SELECT event_id, payload_json FROM events{table_suffix} WHERE project_id = ? ORDER BY event_id",
            (project_id,),
        ),
        "manual_labels": _fetch_rows(
            connection,
            f"SELECT event_id, label FROM manual_labels{table_suffix} WHERE project_id = ? ORDER BY event_id",
            (project_id,),
        ),
        "settings": _fetch_rows(
            connection,
            f"SELECT key, value_json FROM settings{table_suffix} WHERE project_id = ? ORDER BY key",
            (project_id,),
        ),
        "metadata": _fetch_rows(
            connection,
            f"SELECT key, value FROM metadata{table_suffix} WHERE project_id = ? ORDER BY key",
            (project_id,),
        ),
        "import_history": _fetch_rows(
            connection,
            (
                f"SELECT id, source, path, event_count, imported_at FROM import_history{table_suffix} "
                "WHERE project_id = ? ORDER BY id"
            ),
            (project_id,),
        ),
        "automation_reviews": _fetch_rows(
            connection,
            (
                f"SELECT activity, status, note, updated_at FROM automation_reviews{table_suffix} "
                "WHERE project_id = ? ORDER BY activity"
            ),
            (project_id,),
        ),
    }


def _fetch_rows(
    connection: sqlite3.Connection,
    statement: str,
    parameters: tuple[object, ...] = (),
) -> list[list[object]]:
    return [list(row) for row in connection.execute(statement, parameters).fetchall()]


def _dataset_counts(snapshot: dict[str, list[list[object]]]) -> dict[str, int]:
    return {name: len(rows) for name, rows in snapshot.items()}


def _dataset_hash(snapshot: dict[str, list[list[object]]]) -> str:
    return hashlib.sha256(_canonical_json(snapshot).encode("utf-8")).hexdigest()


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


@dataclass(frozen=True)
class MigrationReport:
    previous_version: int
    schema_version: int
    status: str
    applied_migrations: tuple[int, ...]
    backup_name: str = ""
    integrity_status: str = "passed"
    wal_status: str = "passed"
    backup_cleanup_status: str = "passed"


def migrate_database(
    db_path: Path,
    *,
    fault_injector: Callable[[int], None] | None = None,
) -> MigrationReport:
    """Bring one local database to the checked-in schema without mutating newer files."""

    validate_migration_registry()
    with _MIGRATION_LOCK, _acquire_process_lock(db_path.parent):
        existed_before_open = db_path.exists()
        if existed_before_open:
            _preflight_existing_database(db_path)
            _delete_stale_migration_backup_temps(db_path)
        connection = _connect(db_path)
        pseudonym_key: bytes | None = None
        try:
            # The v4 rewrite can make older cells unreachable. SQLite must
            # overwrite those cells rather than leaving raw capture values in
            # a freelist page while the privacy migration is in progress.
            connection.execute("PRAGMA secure_delete = ON")
            connection.execute("BEGIN IMMEDIATE")
            previous_version = _inspect_schema(connection)
            if previous_version > CURRENT_SCHEMA_VERSION:
                raise UnsupportedSchemaError(
                    f"Database schema version {previous_version} is newer than this app supports."
                )
            _assert_integrity(connection)
            if previous_version == CURRENT_SCHEMA_VERSION:
                pseudonym_key = load_or_create_pseudonym_key(db_path.parent, allow_create=False)
                _assert_pseudonym_key_matches(connection, pseudonym_key)
                connection.execute("COMMIT")
                report = MigrationReport(
                    previous_version=previous_version,
                    schema_version=previous_version,
                    status="current",
                    applied_migrations=(),
                )
            else:
                backup_name = ""
                # A pre-v4 snapshot can contain raw capture fields.  The
                # migration itself is atomic, so retaining a second plaintext
                # copy would add exposure without improving recovery.  A
                # later backup feature may create an encrypted safe snapshot.
                if existed_before_open and previous_version >= 4:
                    backup_name = _create_secure_backup(db_path, previous_version).name
                pseudonym_key = (
                    load_or_create_pseudonym_key(db_path.parent)
                    if previous_version < 4
                    else load_or_create_pseudonym_key(db_path.parent, allow_create=False)
                )
                if previous_version >= 4:
                    _assert_pseudonym_key_matches(connection, pseudonym_key)
                applied: list[int] = []
                for migration in MIGRATIONS:
                    if migration.version <= previous_version:
                        continue
                    migration.apply(connection, pseudonym_key=pseudonym_key)
                    if fault_injector is not None:
                        fault_injector(migration.version)
                    connection.execute(
                        "INSERT INTO schema_migrations(version, name, checksum, applied_at) VALUES(?, ?, ?, ?)",
                        (
                            migration.version,
                            migration.name,
                            migration.checksum,
                            datetime.now(timezone.utc).isoformat(),
                        ),
                    )
                    connection.execute(f"PRAGMA user_version = {migration.version}")
                    applied.append(migration.version)
                _assert_schema_matches_version(connection, CURRENT_SCHEMA_VERSION)
                _assert_integrity(connection)
                connection.execute("COMMIT")
                report = MigrationReport(
                    previous_version=previous_version,
                    schema_version=CURRENT_SCHEMA_VERSION,
                    status="migrated",
                    applied_migrations=tuple(applied),
                    backup_name=backup_name,
                )
        except Exception as error:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            try:
                prune_migration_backups(db_path)
            except OSError:
                pass
            if isinstance(error, (MigrationError, UnsupportedSchemaError)):
                raise
            raise MigrationError(
                "Database migration failed; the original database was left unchanged."
            ) from error
        finally:
            connection.close()
        if pseudonym_key is not None and _privacy_cleanup_required(db_path):
            try:
                _complete_privacy_cleanup(db_path)
            except (MigrationError, sqlite3.Error, OSError) as error:
                raise MigrationError(
                    "Privacy migration is incomplete because local SQLite cleanup could not be verified; refusing startup."
                ) from error
        else:
            try:
                _configure_wal(db_path)
            except (MigrationError, sqlite3.Error, OSError):
                report = replace(report, wal_status="warning")
        if report.status == "migrated":
            try:
                prune_migration_backups(db_path)
            except OSError:
                report = replace(report, backup_cleanup_status="warning")
        return report


def validate_migration_registry(migrations: Sequence[Migration] = MIGRATIONS) -> None:
    expected_versions = list(range(1, CURRENT_SCHEMA_VERSION + 1))
    actual_versions = [migration.version for migration in migrations]
    if actual_versions != expected_versions:
        raise MigrationInvariantError(
            f"Migration versions must be contiguous from 1 through {CURRENT_SCHEMA_VERSION}; found {actual_versions}."
        )
    for migration in migrations:
        checksum = _migration_checksum(migration.statements, migration.legacy_steps)
        if migration.checksum != checksum:
            raise MigrationInvariantError(
                f"Migration {migration.version} checksum changed. Add a new migration instead of rewriting applied history."
            )


def _connect(db_path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(db_path, timeout=5, isolation_level=None)
    connection.execute("PRAGMA busy_timeout = 5000")
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def _preflight_existing_database(db_path: Path) -> None:
    connection = _connect_readonly(db_path)
    try:
        schema_version = _inspect_schema(connection)
        if schema_version > CURRENT_SCHEMA_VERSION:
            raise UnsupportedSchemaError(
                f"Database schema version {schema_version} is newer than this app supports."
            )
    finally:
        connection.close()


def _connect_readonly(db_path: Path) -> sqlite3.Connection:
    database_uri = f"{db_path.resolve().as_uri()}?mode=ro"
    connection = sqlite3.connect(database_uri, uri=True, timeout=5, isolation_level=None)
    connection.execute("PRAGMA query_only = ON")
    connection.execute("PRAGMA busy_timeout = 5000")
    return connection


def _inspect_schema(connection: sqlite3.Connection) -> int:
    user_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
    table_names = {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    }
    has_ledger = "schema_migrations" in table_names
    if user_version > CURRENT_SCHEMA_VERSION:
        return user_version
    if user_version == 0:
        if has_ledger:
            raise MigrationError("Database has a migration ledger but no schema version; refusing unsafe repair.")
        if not table_names:
            return 0
        if frozenset(table_names) not in _KNOWN_LEGACY_TABLE_SETS:
            raise MigrationError("Database is not a recognized OpsMineFlow legacy schema; refusing to initialize it.")
        _assert_legacy_schema_matches(connection)
        return 0
    if not has_ledger:
        raise MigrationError("Database schema version has no migration ledger; refusing unsafe repair.")
    _assert_ledger_matches(connection, user_version)
    _assert_schema_matches_version(connection, user_version)
    return user_version


def _assert_ledger_matches(connection: sqlite3.Connection, schema_version: int) -> None:
    rows = connection.execute(
        "SELECT version, name, checksum FROM schema_migrations ORDER BY version"
    ).fetchall()
    expected = [migration for migration in MIGRATIONS if migration.version <= schema_version]
    if len(rows) != len(expected):
        raise MigrationError("Database migration ledger does not match its schema version.")
    for row, migration in zip(rows, expected, strict=True):
        if (int(row[0]), str(row[1]), str(row[2])) != (
            migration.version,
            migration.name,
            migration.checksum,
        ):
            raise MigrationError("Database migration ledger contains an unknown or rewritten migration.")


def _assert_legacy_schema_matches(connection: sqlite3.Connection) -> None:
    for table_name, expected_signature in _LEGACY_TABLE_SIGNATURES.items():
        if not _table_exists(connection, table_name):
            continue
        signature = _table_signature(connection, table_name)
        if table_name == "automation_reviews":
            if signature in {expected_signature, _LEGACY_AUTOMATION_REVIEW_WITH_NOTE}:
                continue
        elif signature == expected_signature:
            continue
        raise MigrationError("Database does not match a recognized OpsMineFlow legacy schema; refusing migration.")


def _assert_schema_matches_version(connection: sqlite3.Connection, schema_version: int) -> None:
    expected_signatures = _SCHEMA_SIGNATURES.get(schema_version)
    if expected_signatures is None:
        raise MigrationError(f"Database schema version {schema_version} has no checked-in schema signature.")
    actual_table_names = {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    }
    if actual_table_names != set(expected_signatures):
        raise MigrationError("Database table set does not match its recorded schema version; refusing unsafe repair.")
    for table_name, allowed_signatures in expected_signatures.items():
        if _table_signature(connection, table_name) not in allowed_signatures:
            raise MigrationError(
                "Database table layout does not match its recorded schema version; refusing unsafe repair."
            )


def _table_exists(connection: sqlite3.Connection, table_name: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table_name,)
    ).fetchone() is not None


def _table_signature(connection: sqlite3.Connection, table_name: str) -> tuple[tuple[str, str, int, str | None, int], ...]:
    return tuple(
        (str(row[1]), str(row[2]).upper(), int(row[3]), None if row[4] is None else str(row[4]), int(row[5]))
        for row in connection.execute(f"PRAGMA table_info({table_name})").fetchall()
    )


def _assert_integrity(connection: sqlite3.Connection) -> None:
    integrity_rows = [str(row[0]) for row in connection.execute("PRAGMA integrity_check").fetchall()]
    if integrity_rows != ["ok"]:
        raise MigrationError("SQLite integrity check failed; migration was not started.")
    foreign_key_rows = connection.execute("PRAGMA foreign_key_check").fetchall()
    if foreign_key_rows:
        raise MigrationError("SQLite foreign key check failed; migration was not started.")


def _create_secure_backup(db_path: Path, schema_version: int) -> Path:
    backup_dir = db_path.parent / "backups"
    if backup_dir.is_symlink():
        raise MigrationError("The backup directory must not be a symlink.")
    backup_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(backup_dir, 0o700)
    file_descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{db_path.stem}.v{schema_version}.", suffix=".sqlite3.tmp", dir=backup_dir
    )
    os.close(file_descriptor)
    temporary_path = Path(temporary_name)
    target_path = backup_dir / (
        f"{db_path.stem}.v{schema_version}.{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}."
        f"{secrets.token_hex(4)}.sqlite3"
    )
    try:
        source = _connect(db_path)
        try:
            with sqlite3.connect(temporary_path, isolation_level=None) as destination:
                source.backup(destination)
                _assert_integrity(destination)
        finally:
            source.close()
        _fsync_file(temporary_path)
        os.replace(temporary_path, target_path)
        _fsync_directory(backup_dir)
        return target_path
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise


def prune_migration_backups(db_path: Path) -> None:
    backup_paths = _migration_backup_paths(db_path)
    for path in backup_paths[MAX_MIGRATION_BACKUPS:]:
        path.unlink(missing_ok=True)


def delete_migration_backups(db_path: Path) -> None:
    for path in _migration_backup_paths(db_path, include_temporary=True):
        path.unlink(missing_ok=True)


def _delete_stale_migration_backup_temps(db_path: Path) -> None:
    for path in _migration_backup_paths(db_path, include_temporary=True):
        if path.name.endswith(".sqlite3.tmp"):
            path.unlink(missing_ok=True)


def _migration_backup_paths(db_path: Path, *, include_temporary: bool = False) -> list[Path]:
    backup_dir = db_path.parent / "backups"
    if not backup_dir.exists() or backup_dir.is_symlink():
        return []
    patterns = [f"{db_path.stem}.v*.sqlite3"]
    if include_temporary:
        patterns.append(f".{db_path.stem}.v*.sqlite3.tmp")
    return sorted(
        (
            path
            for pattern in patterns
            for path in backup_dir.glob(pattern)
            if path.is_file() or path.is_symlink()
        ),
        key=lambda path: path.stat(follow_symlinks=False).st_mtime,
        reverse=True,
    )


def _configure_wal(db_path: Path) -> None:
    connection: sqlite3.Connection | None = None
    try:
        connection = _connect(db_path)
        journal_mode = str(connection.execute("PRAGMA journal_mode = WAL").fetchone()[0]).casefold()
        if journal_mode != "wal":
            raise MigrationError("SQLite could not enable WAL mode.")
        checkpoint = connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        if checkpoint is None or int(checkpoint[0]) != 0:
            raise MigrationError("SQLite WAL checkpoint did not complete.")
    except (sqlite3.Error, OSError) as error:
        raise MigrationError("SQLite WAL checkpoint could not be completed after the schema migration.") from error
    finally:
        if connection is not None:
            connection.close()


def _vacuum_privacy_migration(db_path: Path) -> None:
    """Rebuild a just-sanitized database so pre-v4 payload bytes are gone."""

    connection: sqlite3.Connection | None = None
    try:
        connection = _connect(db_path)
        connection.execute("PRAGMA secure_delete = ON")
        connection.execute("VACUUM")
    except (sqlite3.Error, OSError) as error:
        raise MigrationError("SQLite could not compact the completed privacy migration.") from error
    finally:
        if connection is not None:
            connection.close()


def _privacy_cleanup_required(db_path: Path) -> bool:
    connection = _connect(db_path)
    try:
        row = connection.execute(
            "SELECT value FROM workspace_metadata WHERE key = ?",
            (PRIVACY_CLEANUP_METADATA_KEY,),
        ).fetchone()
        return row is None or str(row[0]) != "complete"
    finally:
        connection.close()


def _complete_privacy_cleanup(db_path: Path) -> None:
    """Checkpoint, compact, and durably mark the v4 privacy rewrite complete."""

    _configure_wal(db_path)
    _vacuum_privacy_migration(db_path)
    _configure_wal(db_path)
    connection = _connect(db_path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """
            INSERT INTO workspace_metadata(key, value) VALUES(?, 'complete')
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (PRIVACY_CLEANUP_METADATA_KEY,),
        )
        connection.execute("COMMIT")
    except Exception:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise
    finally:
        connection.close()
    _configure_wal(db_path)


def _migration_checksum(statements: Sequence[str], legacy_steps: Sequence[str]) -> str:
    canonical = "\n".join(" ".join(statement.split()) for statement in statements)
    canonical += "\nlegacy:" + ",".join(legacy_steps)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _fsync_file(path: Path) -> None:
    with path.open("rb") as file_handle:
        os.fsync(file_handle.fileno())


def _fsync_directory(path: Path) -> None:
    directory_fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


@contextmanager
def _acquire_process_lock(data_dir: Path):
    lock_path = data_dir / ".opsmineflow-migration.lock"
    lock_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    lock_handle = os.fdopen(lock_fd, "a+", encoding="utf-8")
    locked = False
    try:
        os.fchmod(lock_handle.fileno(), 0o600)
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
        locked = True
        yield
    finally:
        if locked:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
        lock_handle.close()


if __name__ == "__main__":
    validate_migration_registry()
    print(f"Migration registry is valid through schema version {CURRENT_SCHEMA_VERSION}.")
