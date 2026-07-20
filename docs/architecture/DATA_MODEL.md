# Data Model

OpsMineFlow uses a standard event record as the common contract between importers, analysis, API, UI, and exports.

## Core Entities

- Event: one normalized observed work activity.
- Case: a group of events representing one business instance.
- Session: a contiguous work session.
- Business label: a rule-based or manually assigned work category.
- Process map: activities and transitions derived from case-ordered events.
- Automation candidate: a scored task or pattern that may deserve improvement review.

## Storage

The product local workflow uses SQLite in the user's application data directory by default. Tests and explicit callers can still use an in-memory `EventStore`. Storage remains local-only and can be redirected with `OPSMINEFLOW_DATA_DIR`.

### Schema Evolution and Recovery

Persistent databases use `PRAGMA user_version` together with an append-only `schema_migrations` ledger. OpsMineFlow applies ordered migrations only at startup, in one SQLite transaction. A migration never rewrites an already-applied migration: a schema change requires a new, sequential migration entry and a matching registry checksum.

Before upgrading an existing recognized database, the app creates a SQLite online-backup snapshot in the local `backups/` directory. The backup directory is owner-only and the snapshot file is owner-read/write only. The app retains at most the three newest migration snapshots after an attempt that created a snapshot, whether that attempt commits or rolls back. The app verifies database integrity and foreign-key consistency before and after migration, then checkpoints WAL after a successful upgrade. A post-commit WAL checkpoint warning does not roll back a completed schema migration; diagnostics reports it separately for follow-up.

If a database was created by a newer app version, has an unknown migration ledger, or is not a recognized legacy schema, OpsMineFlow fails closed. It does not create tables, overwrite the database, seed sample data, or attempt an automatic restore. Keep the original database and use the pre-upgrade snapshot for manual recovery with a compatible build. **Delete Data** removes both active analysis records and migration snapshots; filesystem or Time Machine backups are outside the app's control.
