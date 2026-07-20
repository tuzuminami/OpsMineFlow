from __future__ import annotations

import hashlib
import json
import sqlite3
import stat
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from opsmineflow_api.migrations import (
    CURRENT_SCHEMA_VERSION,
    MIGRATIONS,
    MigrationError,
    MigrationInvariantError,
    UnsupportedSchemaError,
    validate_migration_registry,
)
from opsmineflow_api.storage import EventStore
from opsmineflow_mining import load_events_from_csv


class StorageMigrationTests(unittest.TestCase):
    def test_legacy_database_migrates_once_and_preserves_data(self) -> None:
        events = load_events_from_csv("data/sample/sample_events.csv")
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "opsmineflow.sqlite3"
            _create_legacy_database(db_path, events)

            store = EventStore(db_path=db_path)
            diagnostics = store.diagnostics()
            reopened = EventStore(db_path=db_path)
            backup_paths = list((db_path.parent / "backups").glob("*.sqlite3"))
            backup_mode = stat.S_IMODE(backup_paths[0].stat().st_mode)
            backup_dir_mode = stat.S_IMODE(backup_paths[0].parent.stat().st_mode)

            with sqlite3.connect(db_path) as connection:
                self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], CURRENT_SCHEMA_VERSION)
                ledger = connection.execute("SELECT version, name, checksum FROM schema_migrations").fetchall()
                self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
                self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
            with sqlite3.connect(backup_paths[0]) as backup_connection:
                backup_event_count = backup_connection.execute("SELECT COUNT(*) FROM events").fetchone()[0]
                backup_columns = {
                    row[1] for row in backup_connection.execute("PRAGMA table_info(automation_reviews)").fetchall()
                }

        self.assertEqual(len(store.events), len(events))
        self.assertEqual(store.manual_labels[events[0].event_id], "Reviewed")
        self.assertEqual(store.get_settings()["retention_days"], 14)
        self.assertEqual(store.list_import_history()[0]["source"], "legacy_csv")
        self.assertEqual(store.list_import_history()[0]["path"], "legacy.csv")
        self.assertEqual(store.automation_reviews["社内確認"], "adopted")
        self.assertEqual(diagnostics["schema_version"], CURRENT_SCHEMA_VERSION)
        self.assertEqual(diagnostics["migration_status"], "migrated")
        self.assertTrue(diagnostics["migration_backup_created"])
        self.assertNotIn("backup_path", diagnostics)
        self.assertEqual(reopened.diagnostics()["migration_status"], "current")
        self.assertEqual(len(backup_paths), 1)
        self.assertEqual(backup_mode, 0o600)
        self.assertEqual(backup_dir_mode, 0o700)
        self.assertEqual(backup_event_count, len(events))
        self.assertNotIn("note", backup_columns)
        self.assertEqual(
            ledger,
            [
                (1, "baseline_event_store", MIGRATIONS[0].checksum),
                (2, "redact_import_history_paths", MIGRATIONS[1].checksum),
            ],
        )

    def test_all_historical_v01_table_sets_upgrade_to_the_baseline_schema(self) -> None:
        events = load_events_from_csv("data/sample/sample_events.csv")
        for legacy_table_count in (3, 5, 6):
            with self.subTest(legacy_table_count=legacy_table_count), tempfile.TemporaryDirectory() as temp_dir:
                db_path = Path(temp_dir) / "opsmineflow.sqlite3"
                _create_legacy_database(db_path, events[:1], table_count=legacy_table_count)

                store = EventStore(db_path=db_path)
                with sqlite3.connect(db_path) as connection:
                    table_names = {
                        row[0]
                        for row in connection.execute(
                            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
                        ).fetchall()
                    }

                self.assertEqual(len(store.events), 1)
                self.assertEqual(
                    table_names,
                    {
                        "events",
                        "manual_labels",
                        "settings",
                        "metadata",
                        "import_history",
                        "automation_reviews",
                        "schema_migrations",
                    },
                )

    def test_wal_legacy_database_snapshot_includes_committed_rows(self) -> None:
        events = load_events_from_csv("data/sample/sample_events.csv")
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "opsmineflow.sqlite3"
            _create_legacy_database(db_path, events[:1])
            with sqlite3.connect(db_path) as connection:
                self.assertEqual(connection.execute("PRAGMA journal_mode = WAL").fetchone()[0], "wal")
                connection.execute(
                    "INSERT INTO events(event_id, payload_json) VALUES(?, ?)",
                    (events[1].event_id, json.dumps(events[1].to_dict(), ensure_ascii=False)),
                )
            store = EventStore(db_path=db_path)
            backup_path = next((db_path.parent / "backups").glob("*.sqlite3"))
            with sqlite3.connect(backup_path) as backup_connection:
                backup_event_count = backup_connection.execute("SELECT COUNT(*) FROM events").fetchone()[0]

        self.assertEqual(len(store.events), 2)
        self.assertEqual(backup_event_count, 2)

    def test_failed_migration_rolls_back_and_leaves_preupgrade_snapshot(self) -> None:
        events = load_events_from_csv("data/sample/sample_events.csv")
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "opsmineflow.sqlite3"
            _create_legacy_database(db_path, events[:1])

            def fail_after_first_migration(_version: int) -> None:
                raise RuntimeError("intentional migration fault")

            with self.assertRaises(MigrationError):
                EventStore(db_path=db_path, migration_fault_injector=fail_after_first_migration)

            with sqlite3.connect(db_path) as connection:
                version = connection.execute("PRAGMA user_version").fetchone()[0]
                event_count = connection.execute("SELECT COUNT(*) FROM events").fetchone()[0]
                ledger_exists = connection.execute(
                    "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name = 'schema_migrations'"
                ).fetchone()[0]
            backup_paths = list((db_path.parent / "backups").glob("*.sqlite3"))

        self.assertEqual(version, 0)
        self.assertEqual(event_count, 1)
        self.assertEqual(ledger_exists, 0)
        self.assertEqual(len(backup_paths), 1)

    def test_wal_checkpoint_warning_does_not_report_a_committed_migration_as_rolled_back(self) -> None:
        events = load_events_from_csv("data/sample/sample_events.csv")
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "opsmineflow.sqlite3"
            _create_legacy_database(db_path, events[:1])
            with patch(
                "opsmineflow_api.migrations._configure_wal",
                side_effect=sqlite3.OperationalError("intentional WAL checkpoint failure"),
            ):
                store = EventStore(db_path=db_path)

            with sqlite3.connect(db_path) as connection:
                version = connection.execute("PRAGMA user_version").fetchone()[0]
                ledger_count = connection.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0]

        self.assertEqual(version, CURRENT_SCHEMA_VERSION)
        self.assertEqual(ledger_count, CURRENT_SCHEMA_VERSION)
        self.assertEqual(store.diagnostics()["migration_status"], "migrated")
        self.assertEqual(store.diagnostics()["wal_status"], "warning")

    def test_backup_cleanup_warning_does_not_hide_a_committed_migration(self) -> None:
        events = load_events_from_csv("data/sample/sample_events.csv")
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "opsmineflow.sqlite3"
            _create_legacy_database(db_path, events[:1])
            with patch(
                "opsmineflow_api.migrations.prune_migration_backups",
                side_effect=PermissionError("intentional backup cleanup failure"),
            ):
                store = EventStore(db_path=db_path)

            with sqlite3.connect(db_path) as connection:
                version = connection.execute("PRAGMA user_version").fetchone()[0]

        self.assertEqual(version, CURRENT_SCHEMA_VERSION)
        self.assertEqual(store.diagnostics()["migration_status"], "migrated")
        self.assertEqual(store.diagnostics()["backup_cleanup_status"], "warning")

    def test_failed_migration_backup_retention_is_bounded(self) -> None:
        events = load_events_from_csv("data/sample/sample_events.csv")
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "opsmineflow.sqlite3"
            _create_legacy_database(db_path, events[:1])

            def fail_after_first_migration(_version: int) -> None:
                raise RuntimeError("intentional migration fault")

            for _ in range(5):
                with self.assertRaises(MigrationError):
                    EventStore(db_path=db_path, migration_fault_injector=fail_after_first_migration)
            backup_paths = list((db_path.parent / "backups").glob("*.sqlite3"))

        self.assertEqual(len(backup_paths), 3)

    def test_delete_data_removes_migration_snapshots(self) -> None:
        events = load_events_from_csv("data/sample/sample_events.csv")
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "opsmineflow.sqlite3"
            _create_legacy_database(db_path, events[:1])
            store = EventStore(db_path=db_path)
            self.assertTrue(list((db_path.parent / "backups").glob("*.sqlite3")))
            interrupted_snapshot = db_path.parent / "backups" / ".opsmineflow.v0.interrupted.sqlite3.tmp"
            interrupted_snapshot.write_bytes(b"interrupted migration backup")

            store.clear()
            backup_paths = list((db_path.parent / "backups").glob("*.sqlite3*"))

        self.assertEqual(backup_paths, [])

    def test_newer_schema_is_rejected_without_mutating_database(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "opsmineflow.sqlite3"
            with sqlite3.connect(db_path) as connection:
                connection.execute("PRAGMA user_version = 99")
            before = _sha256(db_path)

            with patch("opsmineflow_api.migrations._connect", side_effect=AssertionError("must stay read-only")):
                with self.assertRaises(UnsupportedSchemaError):
                    EventStore(db_path=db_path)

            after = _sha256(db_path)

        self.assertEqual(after, before)

    def test_stale_interrupted_backup_is_removed_before_next_migration(self) -> None:
        events = load_events_from_csv("data/sample/sample_events.csv")
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "opsmineflow.sqlite3"
            _create_legacy_database(db_path, events[:1])
            backup_dir = db_path.parent / "backups"
            backup_dir.mkdir(mode=0o700)
            stale_snapshot = backup_dir / ".opsmineflow.v0.interrupted.sqlite3.tmp"
            stale_snapshot.write_bytes(b"interrupted migration backup")

            EventStore(db_path=db_path)

            remaining_temporary = list(backup_dir.glob("*.sqlite3.tmp"))

        self.assertEqual(remaining_temporary, [])

    def test_newer_wal_schema_is_preflighted_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "opsmineflow.sqlite3"
            writer = sqlite3.connect(db_path)
            try:
                self.assertEqual(writer.execute("PRAGMA journal_mode = WAL").fetchone()[0], "wal")
                writer.execute("CREATE TABLE future_data(value TEXT NOT NULL)")
                writer.execute("INSERT INTO future_data(value) VALUES('newer-schema')")
                writer.execute("PRAGMA user_version = 99")
                writer.commit()
                wal_path = Path(f"{db_path}-wal")
                backup_dir = db_path.parent / "backups"
                backup_dir.mkdir(mode=0o700)
                recovery_temp = backup_dir / ".opsmineflow.v99.recovery.sqlite3.tmp"
                recovery_temp.write_bytes(b"newer app recovery artifact")
                before = {path.name: _sha256(path) for path in (db_path, wal_path, recovery_temp)}

                with self.assertRaises(UnsupportedSchemaError):
                    EventStore(db_path=db_path)

                after = {path.name: _sha256(path) for path in (db_path, wal_path, recovery_temp)}
            finally:
                writer.close()

        self.assertEqual(after, before)

    def test_unknown_unversioned_database_is_rejected_without_initializing_it(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "opsmineflow.sqlite3"
            with sqlite3.connect(db_path) as connection:
                connection.execute("CREATE TABLE unrelated_data(value TEXT NOT NULL)")

            with self.assertRaises(MigrationError):
                EventStore(db_path=db_path)

            with sqlite3.connect(db_path) as connection:
                table_names = {
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
                    ).fetchall()
                }

        self.assertEqual(table_names, {"unrelated_data"})

    def test_unrecognized_legacy_table_shape_is_rejected_before_backup(self) -> None:
        events = load_events_from_csv("data/sample/sample_events.csv")
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "opsmineflow.sqlite3"
            _create_legacy_database(db_path, events[:1])
            with sqlite3.connect(db_path) as connection:
                connection.execute("ALTER TABLE events ADD COLUMN unexpected_column TEXT")

            with self.assertRaises(MigrationError):
                EventStore(db_path=db_path)

            backup_paths = list((db_path.parent / "backups").glob("*.sqlite3"))

        self.assertEqual(backup_paths, [])

    def test_current_schema_with_unrecognized_table_layout_is_rejected_before_load(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "opsmineflow.sqlite3"
            EventStore(db_path=db_path)
            with sqlite3.connect(db_path) as connection:
                connection.execute("ALTER TABLE events ADD COLUMN unexpected_column TEXT")

            with self.assertRaises(MigrationError):
                EventStore(db_path=db_path)

            backup_paths = list((db_path.parent / "backups").glob("*.sqlite3"))

        self.assertEqual(backup_paths, [])

    def test_migration_registry_rejects_rewritten_history(self) -> None:
        validate_migration_registry()
        rewritten = replace(MIGRATIONS[0], statements=(*MIGRATIONS[0].statements, "CREATE TABLE rewritten_history(value TEXT)"))

        with self.assertRaises(MigrationInvariantError):
            validate_migration_registry((rewritten,))


def _create_legacy_database(db_path: Path, events: list[object], *, table_count: int = 6) -> None:
    if table_count not in {3, 5, 6}:
        raise ValueError("Legacy fixtures support the historical 3, 5, or 6 table shapes.")
    with sqlite3.connect(db_path) as connection:
        connection.execute("CREATE TABLE events(event_id TEXT PRIMARY KEY, payload_json TEXT NOT NULL)")
        connection.execute("CREATE TABLE manual_labels(event_id TEXT PRIMARY KEY, label TEXT NOT NULL)")
        connection.execute("CREATE TABLE settings(key TEXT PRIMARY KEY, value_json TEXT NOT NULL)")
        if table_count >= 5:
            connection.execute("CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            connection.execute(
                "CREATE TABLE import_history(id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT NOT NULL, path TEXT NOT NULL, event_count INTEGER NOT NULL, imported_at TEXT NOT NULL)"
            )
        if table_count >= 6:
            connection.execute(
                "CREATE TABLE automation_reviews(activity TEXT PRIMARY KEY, status TEXT NOT NULL, updated_at TEXT NOT NULL)"
            )
        connection.executemany(
            "INSERT INTO events(event_id, payload_json) VALUES(?, ?)",
            [(event.event_id, json.dumps(event.to_dict(), ensure_ascii=False)) for event in events],
        )
        if events:
            connection.execute("INSERT INTO manual_labels(event_id, label) VALUES(?, ?)", (events[0].event_id, "Reviewed"))
        connection.execute("INSERT INTO settings(key, value_json) VALUES(?, ?)", ("retention_days", "14"))
        if table_count >= 5:
            connection.execute("INSERT INTO metadata(key, value) VALUES(?, ?)", ("initialized", "true"))
            connection.execute(
                "INSERT INTO import_history(source, path, event_count, imported_at) VALUES(?, ?, ?, ?)",
                ("legacy_csv", "legacy.csv", len(events), "2026-07-20T00:00:00+00:00"),
            )
        if table_count >= 6:
            connection.execute(
                "INSERT INTO automation_reviews(activity, status, updated_at) VALUES(?, ?, ?)",
                ("社内確認", "adopted", "2026-07-20T00:00:00+00:00"),
            )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
