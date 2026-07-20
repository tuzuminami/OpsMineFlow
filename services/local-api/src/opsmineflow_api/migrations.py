from __future__ import annotations

import hashlib
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

import fcntl


CURRENT_SCHEMA_VERSION = 1
MAX_MIGRATION_BACKUPS = 3
_MIGRATION_LOCK = threading.RLock()
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

    def apply(self, connection: sqlite3.Connection) -> None:
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

MIGRATIONS: tuple[Migration, ...] = (
    Migration(
        version=1,
        name="baseline_event_store",
        checksum=_MIGRATION_001_CHECKSUM,
        statements=_MIGRATION_001_STATEMENTS,
        legacy_steps=_MIGRATION_001_LEGACY_STEPS,
    ),
)


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
        try:
            connection.execute("BEGIN IMMEDIATE")
            previous_version = _inspect_schema(connection)
            if previous_version > CURRENT_SCHEMA_VERSION:
                raise UnsupportedSchemaError(
                    f"Database schema version {previous_version} is newer than this app supports."
                )
            _assert_integrity(connection)
            if previous_version == CURRENT_SCHEMA_VERSION:
                connection.execute("COMMIT")
                report = MigrationReport(
                    previous_version=previous_version,
                    schema_version=previous_version,
                    status="current",
                    applied_migrations=(),
                )
            else:
                backup_name = ""
                if existed_before_open:
                    backup_name = _create_secure_backup(db_path, previous_version).name
                applied: list[int] = []
                for migration in MIGRATIONS:
                    if migration.version <= previous_version:
                        continue
                    migration.apply(connection)
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
                "Database migration failed; the original database was left unchanged and any pre-migration backup was retained."
            ) from error
        finally:
            connection.close()
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
