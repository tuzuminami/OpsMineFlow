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
    LEGACY_PROJECT_ID,
    MIGRATIONS,
    MigrationError,
    MigrationInvariantError,
    PSEUDONYM_KEY_FILENAME,
    PRIVACY_CLEANUP_METADATA_KEY,
    UnsupportedSchemaError,
    migrate_database,
    validate_migration_registry,
)
from opsmineflow_api.storage import EventStore
from opsmineflow_mining import load_events_from_csv


class StorageMigrationTests(unittest.TestCase):
    def test_fresh_database_applies_v4_and_creates_a_durable_empty_legacy_project(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "opsmineflow.sqlite3"

            report = migrate_database(db_path)
            reopened = migrate_database(db_path)

            with sqlite3.connect(db_path) as connection:
                project = connection.execute(
                    "SELECT project_id, display_name, origin, revision FROM projects"
                ).fetchone()
                workspace_metadata = dict(connection.execute("SELECT key, value FROM workspace_metadata").fetchall())
                empty_snapshot = _v3_dataset_snapshot(connection, LEGACY_PROJECT_ID)
                ledger = connection.execute("SELECT version FROM schema_migrations ORDER BY version").fetchall()
            key_mode = stat.S_IMODE((db_path.parent / PSEUDONYM_KEY_FILENAME).stat().st_mode)

        self.assertEqual(report.previous_version, 0)
        self.assertEqual(report.applied_migrations, (1, 2, 3, 4))
        self.assertEqual(reopened.status, "current")
        self.assertEqual(project, (LEGACY_PROJECT_ID, "Migrated data", "legacy_migration", 0))
        self.assertEqual(ledger, [(1,), (2,), (3,), (4,)])
        self.assertEqual(workspace_metadata["active_project_id"], LEGACY_PROJECT_ID)
        self.assertEqual(key_mode, 0o600)
        self.assertEqual(workspace_metadata["legacy_v2_before_hash"], _dataset_hash(empty_snapshot))
        self.assertEqual(workspace_metadata["legacy_v3_after_hash"], _dataset_hash(empty_snapshot))
        self.assertEqual(json.loads(workspace_metadata["legacy_v2_before_counts"]), _dataset_counts(empty_snapshot))
        self.assertEqual(json.loads(workspace_metadata["legacy_v3_after_counts"]), _dataset_counts(empty_snapshot))

    def test_legacy_database_migrates_once_and_preserves_data(self) -> None:
        events = load_events_from_csv("data/sample/sample_events.csv")
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "opsmineflow.sqlite3"
            _create_legacy_database(db_path, events)

            store = EventStore(db_path=db_path)
            diagnostics = store.diagnostics()
            reopened = EventStore(db_path=db_path)
            backup_paths = list((db_path.parent / "backups").glob("*.sqlite3"))

            with sqlite3.connect(db_path) as connection:
                self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], CURRENT_SCHEMA_VERSION)
                ledger = connection.execute("SELECT version, name, checksum FROM schema_migrations").fetchall()
                self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
                self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])

        self.assertEqual(len(store.events), len(events))
        self.assertEqual(store.manual_labels[store.events[0].event_id], "Reviewed")
        self.assertEqual(store.get_settings()["retention_days"], 14)
        self.assertEqual(store.list_import_history()[0]["source"], "legacy_csv")
        self.assertEqual(store.list_import_history()[0]["path"], "Imported data")
        self.assertEqual(store.automation_reviews["社内確認"], "adopted")
        self.assertEqual(diagnostics["schema_version"], CURRENT_SCHEMA_VERSION)
        self.assertEqual(diagnostics["migration_status"], "migrated")
        self.assertFalse(diagnostics["migration_backup_created"])
        self.assertNotIn("backup_path", diagnostics)
        self.assertEqual(reopened.diagnostics()["migration_status"], "current")
        self.assertEqual(backup_paths, [])
        self.assertEqual(
            ledger,
            [
                (1, "baseline_event_store", MIGRATIONS[0].checksum),
                (2, "redact_import_history_paths", MIGRATIONS[1].checksum),
                (3, "scope_records_to_projects", MIGRATIONS[2].checksum),
                (4, "minimize_persisted_event_payloads", MIGRATIONS[3].checksum),
            ],
        )

    def test_current_v4_database_refuses_a_missing_or_replaced_pseudonym_key(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "opsmineflow.sqlite3"
            EventStore(db_path=db_path)
            key_path = db_path.parent / PSEUDONYM_KEY_FILENAME
            key_path.unlink()
            with self.assertRaisesRegex(MigrationError, "pseudonym key"):
                EventStore(db_path=db_path)

            # Restoring an arbitrary 0600 key is not a recovery mechanism:
            # its verifier must match the database-bound key material.
            key_path.write_bytes(b"x" * 32)
            key_path.chmod(0o600)
            with self.assertRaisesRegex(MigrationError, "does not match"):
                EventStore(db_path=db_path)

    def test_v4_removes_raw_capture_values_from_current_event_payloads(self) -> None:
        source_event = load_events_from_csv("data/sample/sample_events.csv")[0]
        sentinel_values = {
            "user_alias": "PII_SENTINEL_ALIAS",
            "user_hash": "PII_SENTINEL_USER_HASH",
            "window_title": "PII_SENTINEL_TITLE",
            "url": "SECRET_SENTINEL_URL_PATH",
        }
        url_with_userinfo = "https://" + "PRIVATE_URL_USER:PRIVATE_URL_SECRET" + "@127.0.0.1:8443/private-path"
        raw_event = replace(
            source_event,
            event_id="PRIVATE_EVENT_IDENTIFIER",
            source_event_id="PRIVATE_SOURCE_IDENTIFIER",
            case_id="PRIVATE_CASE_IDENTIFIER",
            **sentinel_values,
            window_title_masked="PII_SENTINEL_TITLE",
            url_masked="SECRET_SENTINEL_URL_PATH",
            domain=url_with_userinfo,
            metadata_json=json.dumps({"unknown": "SECRET_SENTINEL_METADATA"}),
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "opsmineflow.sqlite3"
            migrate_database(db_path)
            with sqlite3.connect(db_path) as connection:
                connection.execute("DELETE FROM schema_migrations WHERE version = 4")
                connection.execute("PRAGMA user_version = 3")
                connection.execute(
                    "INSERT INTO events(project_id, event_id, payload_json) VALUES(?, ?, ?)",
                    (LEGACY_PROJECT_ID, raw_event.event_id, json.dumps(raw_event.to_dict(), ensure_ascii=False)),
                )
                connection.execute(
                    "INSERT INTO import_history(project_id, id, source, path, event_count, imported_at) VALUES(?, ?, ?, ?, ?, ?)",
                    (LEGACY_PROJECT_ID, 1, "csv", "PII_SENTINEL_FILENAME.csv", 1, "2026-07-20T00:00:00+00:00"),
                )
            report = migrate_database(db_path)
            with sqlite3.connect(db_path) as connection:
                stored_event_id, payload = connection.execute("SELECT event_id, payload_json FROM events").fetchone()
                import_path = connection.execute("SELECT path FROM import_history").fetchone()[0]
            durable_contents = "\n".join(
                artifact.read_bytes().decode("utf-8", errors="replace")
                for artifact in db_path.parent.iterdir()
                if artifact.is_file()
            )

        self.assertEqual(report.applied_migrations, (4,))
        self.assertEqual(import_path, "CSV import")
        self.assertTrue(str(stored_event_id).startswith("evt_v1_"))
        self.assertIn('"domain":"127.0.0.1"', payload)
        for sentinel in (
            *sentinel_values.values(),
            "PRIVATE_EVENT_IDENTIFIER",
            "PRIVATE_SOURCE_IDENTIFIER",
            "PRIVATE_CASE_IDENTIFIER",
            "PRIVATE_URL_USER",
            "PRIVATE_URL_SECRET",
            "8443",
            "SECRET_SENTINEL_METADATA",
            "PII_SENTINEL_FILENAME",
        ):
            self.assertNotIn(sentinel, payload)
            self.assertNotIn(sentinel, durable_contents)

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
                        "projects",
                        "workspace_metadata",
                    },
                )

    def test_v2_backfill_creates_one_legacy_project_and_preserves_auditable_snapshot(self) -> None:
        events = load_events_from_csv("data/sample/sample_events.csv")
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "opsmineflow.sqlite3"
            _create_v2_database(db_path, events[:2])
            before = _v2_dataset_snapshot(db_path)

            report = migrate_database(db_path)
            reopened = migrate_database(db_path)

            with sqlite3.connect(db_path) as connection:
                connection.execute("PRAGMA foreign_keys = ON")
                project = connection.execute(
                    "SELECT project_id, display_name, origin, revision FROM projects"
                ).fetchone()
                workspace_metadata = dict(
                    connection.execute("SELECT key, value FROM workspace_metadata ORDER BY key").fetchall()
                )
                after = _v3_dataset_snapshot(connection, LEGACY_PROJECT_ID)
                table_names = {
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
                    ).fetchall()
                }
                foreign_keys = {
                    table_name: connection.execute(f"PRAGMA foreign_key_list({table_name})").fetchall()
                    for table_name in (
                        "events",
                        "manual_labels",
                        "settings",
                        "metadata",
                        "import_history",
                        "automation_reviews",
                    )
                }

                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(
                        "INSERT INTO events(project_id, event_id, payload_json) VALUES(?, ?, ?)",
                        ("missing-project", "event-1", "{}"),
                    )
                connection.execute(
                    "INSERT INTO projects(project_id, display_name, origin, created_at, updated_at, revision) "
                    "VALUES(?, ?, ?, ?, ?, ?)",
                    ("d350dc56-7cfe-4df4-ae0a-bfcf9467d9e0", "Second project", "test", "now", "now", 0),
                )
                connection.execute(
                    "INSERT INTO events(project_id, event_id, payload_json) VALUES(?, ?, ?)",
                    ("d350dc56-7cfe-4df4-ae0a-bfcf9467d9e0", events[0].event_id, "{}"),
                )
                connection.execute(
                    "INSERT INTO manual_labels(project_id, event_id, label) VALUES(?, ?, ?)",
                    ("d350dc56-7cfe-4df4-ae0a-bfcf9467d9e0", events[0].event_id, "Second label"),
                )
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(
                        "INSERT INTO manual_labels(project_id, event_id, label) VALUES(?, ?, ?)",
                        ("d350dc56-7cfe-4df4-ae0a-bfcf9467d9e0", "missing-event", "Rejected"),
                    )
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(
                        "INSERT INTO workspace_metadata(key, value) VALUES(?, ?)",
                        ("active_project_id", "missing-project"),
                    )
                scoped_event_counts = connection.execute(
                    "SELECT project_id, COUNT(*) FROM events GROUP BY project_id ORDER BY project_id"
                ).fetchall()
                scoped_label_counts = connection.execute(
                    "SELECT project_id, COUNT(*) FROM manual_labels GROUP BY project_id ORDER BY project_id"
                ).fetchall()
                foreign_key_check = connection.execute("PRAGMA foreign_key_check").fetchall()

        self.assertEqual(report.previous_version, 2)
        self.assertEqual(report.schema_version, 4)
        self.assertEqual(report.applied_migrations, (3, 4))
        self.assertEqual(reopened.status, "current")
        self.assertEqual(project, (LEGACY_PROJECT_ID, "Migrated data", "legacy_migration", 0))
        self.assertEqual(_dataset_counts(after), _dataset_counts(before))
        self.assertEqual(workspace_metadata["active_project_id"], LEGACY_PROJECT_ID)
        self.assertEqual(workspace_metadata["legacy_v2_before_hash"], workspace_metadata["legacy_v3_after_hash"])
        self.assertEqual(workspace_metadata["legacy_v2_before_hash"], _dataset_hash(before))
        self.assertEqual(json.loads(workspace_metadata["legacy_v2_before_counts"]), _dataset_counts(before))
        self.assertEqual(json.loads(workspace_metadata["legacy_v3_after_counts"]), _dataset_counts(after))
        self.assertEqual(
            table_names,
            {
                "automation_reviews",
                "events",
                "import_history",
                "manual_labels",
                "metadata",
                "projects",
                "schema_migrations",
                "settings",
                "workspace_metadata",
            },
        )
        self.assertTrue(all(foreign_keys.values()))
        self.assertEqual(scoped_event_counts, [(LEGACY_PROJECT_ID, 2), ("d350dc56-7cfe-4df4-ae0a-bfcf9467d9e0", 1)])
        self.assertEqual(scoped_label_counts, [(LEGACY_PROJECT_ID, 1), ("d350dc56-7cfe-4df4-ae0a-bfcf9467d9e0", 1)])
        self.assertEqual(foreign_key_check, [])

    def test_v3_interruption_rolls_back_to_v2_and_a_redo_backfills_once(self) -> None:
        events = load_events_from_csv("data/sample/sample_events.csv")
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "opsmineflow.sqlite3"
            _create_v2_database(db_path, events[:1])

            def fail_after_v3(version: int) -> None:
                if version == 3:
                    raise RuntimeError("intentional v3 migration interruption")

            with self.assertRaises(MigrationError):
                migrate_database(db_path, fault_injector=fail_after_v3)

            with sqlite3.connect(db_path) as connection:
                interrupted_version = connection.execute("PRAGMA user_version").fetchone()[0]
                interrupted_ledger = connection.execute(
                    "SELECT version FROM schema_migrations ORDER BY version"
                ).fetchall()
                interrupted_tables = {
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
                    ).fetchall()
                }
                interrupted_events = connection.execute("SELECT COUNT(*) FROM events").fetchone()[0]

            redo = migrate_database(db_path)
            with sqlite3.connect(db_path) as connection:
                project_count = connection.execute("SELECT COUNT(*) FROM projects").fetchone()[0]
                active_project = connection.execute(
                    "SELECT value FROM workspace_metadata WHERE key = 'active_project_id'"
                ).fetchone()[0]
                migrated_events = connection.execute(
                    "SELECT COUNT(*) FROM events WHERE project_id = ?", (LEGACY_PROJECT_ID,)
                ).fetchone()[0]
                foreign_key_check = connection.execute("PRAGMA foreign_key_check").fetchall()

        self.assertEqual(interrupted_version, 2)
        self.assertEqual(interrupted_ledger, [(1,), (2,)])
        self.assertNotIn("projects", interrupted_tables)
        self.assertEqual(interrupted_events, 1)
        self.assertEqual(redo.applied_migrations, (3, 4))
        self.assertEqual(project_count, 1)
        self.assertEqual(active_project, LEGACY_PROJECT_ID)
        self.assertEqual(migrated_events, 1)
        self.assertEqual(foreign_key_check, [])

    def test_wal_legacy_database_migrates_committed_rows_without_a_raw_snapshot(self) -> None:
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
            backup_paths = list((db_path.parent / "backups").glob("*.sqlite3"))

        self.assertEqual(len(store.events), 2)
        self.assertEqual(backup_paths, [])

    def test_failed_migration_rolls_back_without_creating_a_raw_snapshot(self) -> None:
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
        self.assertEqual(backup_paths, [])

    def test_privacy_migration_fails_closed_when_wal_cleanup_cannot_be_verified(self) -> None:
        events = load_events_from_csv("data/sample/sample_events.csv")
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "opsmineflow.sqlite3"
            _create_legacy_database(db_path, events[:1])
            with patch(
                "opsmineflow_api.migrations._configure_wal",
                side_effect=sqlite3.OperationalError("intentional WAL checkpoint failure"),
            ):
                with self.assertRaises(MigrationError):
                    EventStore(db_path=db_path)

            recovered = EventStore(db_path=db_path)

            with sqlite3.connect(db_path) as connection:
                version = connection.execute("PRAGMA user_version").fetchone()[0]
                ledger_count = connection.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0]
                cleanup_marker = connection.execute(
                    "SELECT value FROM workspace_metadata WHERE key = ?",
                    (PRIVACY_CLEANUP_METADATA_KEY,),
                ).fetchone()[0]

        self.assertEqual(version, CURRENT_SCHEMA_VERSION)
        self.assertEqual(ledger_count, CURRENT_SCHEMA_VERSION)
        self.assertEqual(recovered.diagnostics()["migration_status"], "current")
        self.assertEqual(cleanup_marker, "complete")

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

    def test_failed_migration_does_not_create_raw_backup_copies(self) -> None:
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

        self.assertEqual(backup_paths, [])

    def test_project_clear_retains_workspace_migration_snapshots(self) -> None:
        events = load_events_from_csv("data/sample/sample_events.csv")
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "opsmineflow.sqlite3"
            _create_legacy_database(db_path, events[:1])
            store = EventStore(db_path=db_path)
            backup_dir = db_path.parent / "backups"
            backup_dir.mkdir(mode=0o700)
            interrupted_snapshot = db_path.parent / "backups" / ".opsmineflow.v0.interrupted.sqlite3.tmp"
            interrupted_snapshot.write_bytes(b"interrupted migration backup")

            store.clear()
            backup_paths = list((db_path.parent / "backups").glob("*.sqlite3*"))

        self.assertEqual(len(backup_paths), 1)

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


def _create_v2_database(db_path: Path, events: list[object]) -> None:
    _create_legacy_database(db_path, events)
    applied_at = "2026-07-20T00:00:00+00:00"
    with sqlite3.connect(db_path) as connection:
        for migration in MIGRATIONS[:2]:
            migration.apply(connection)
            connection.execute(
                "INSERT INTO schema_migrations(version, name, checksum, applied_at) VALUES(?, ?, ?, ?)",
                (migration.version, migration.name, migration.checksum, applied_at),
            )
            connection.execute(f"PRAGMA user_version = {migration.version}")


def _v2_dataset_snapshot(db_path: Path) -> dict[str, list[list[object]]]:
    with sqlite3.connect(db_path) as connection:
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


def _v3_dataset_snapshot(connection: sqlite3.Connection, project_id: str) -> dict[str, list[list[object]]]:
    return {
        "events": _fetch_rows(
            connection,
            "SELECT event_id, payload_json FROM events WHERE project_id = ? ORDER BY event_id",
            (project_id,),
        ),
        "manual_labels": _fetch_rows(
            connection,
            "SELECT event_id, label FROM manual_labels WHERE project_id = ? ORDER BY event_id",
            (project_id,),
        ),
        "settings": _fetch_rows(
            connection,
            "SELECT key, value_json FROM settings WHERE project_id = ? ORDER BY key",
            (project_id,),
        ),
        "metadata": _fetch_rows(
            connection,
            "SELECT key, value FROM metadata WHERE project_id = ? ORDER BY key",
            (project_id,),
        ),
        "import_history": _fetch_rows(
            connection,
            "SELECT id, source, path, event_count, imported_at FROM import_history "
            "WHERE project_id = ? ORDER BY id",
            (project_id,),
        ),
        "automation_reviews": _fetch_rows(
            connection,
            "SELECT activity, status, note, updated_at FROM automation_reviews "
            "WHERE project_id = ? ORDER BY activity",
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
    canonical = json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
