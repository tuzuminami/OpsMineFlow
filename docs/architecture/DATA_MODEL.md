# Data Model

OpsMineFlow uses a standard event record as the common contract between importers, analysis, API, UI, and exports.

## Core Entities

- Event: one normalized observed work activity.
- Case: an observed/manual source grouping, or a clearly marked low-confidence
  singleton when no source case ID exists.
- Session: a contiguous UTC work session. A session splits only when its
  inactivity gap is greater than the configured threshold (30 minutes by
  default).
- Business label: a rule-based or manually assigned work category.
- Process map: activities and transitions derived from case-ordered events.
- Automation candidate: a scored task or pattern that may deserve improvement review.
- Analysis receipt: the versioned local record of input, used/excluded event
  counts, exclusion reasons, case-correlation confidence, session rule, and
  duration definitions shared by every analysis output. It also carries
  privacy-safe SHA-256 scope and filter fingerprints for comparison across
  exports without exposing source event content.

## Storage

The product local workflow uses SQLite in the user's application data directory by default. Tests and explicit callers can still use an in-memory `EventStore`. Storage remains local-only and can be redirected with `OPSMINEFLOW_DATA_DIR`.

### Project Boundary

A workspace can contain multiple named projects (for example, a client, workstream, or engagement). A project has an opaque UUID, a display name, origin, timestamps, and a revision. The UUID is the storage and API authority; display names are never used as identifiers.

Every user-data relation is scoped by `project_id`: events, manual labels, settings, metadata, import history, automation reviews, and recording-derived audit state. Composite primary/foreign keys keep records from one project from resolving into another, even when event IDs are identical. Reads and writes use an explicit project context and a project revision compare-and-swap check. The workspace's remembered active project is only a UI convenience; it is not a server-side data-access default.

The desktop runtime removes the `project_id` from an allowlisted UI operation payload, validates its canonical UUID form, and sends it as the local `X-OpsMineFlow-Project` header. The local API rejects project-scoped routes without that header. A recording session binds to its project at start and cannot write events into another selected project.

Users may clear the selected project's data without affecting other projects. A project can be deleted only after its event dataset is empty; deleting the final project is prevented by immediately selecting an existing replacement project.

### Schema Evolution and Recovery

Persistent databases use `PRAGMA user_version` together with an append-only `schema_migrations` ledger. OpsMineFlow applies ordered migrations only at startup, in one SQLite transaction. A migration never rewrites an already-applied migration: a schema change requires a new, sequential migration entry and a matching registry checksum.

Schema version 3 introduced project isolation. It atomically rebuilds the scoped tables, creates a deterministic opaque `Migrated data` project for existing records, backfills every legacy row into that project, and records before/after row counts and content fingerprints in workspace metadata. A failed upgrade leaves the prior schema intact; a retry starts from the original legacy state rather than a partial project migration.

Before upgrading an existing recognized database, the app creates a SQLite online-backup snapshot in the local `backups/` directory. The backup directory is owner-only and the snapshot file is owner-read/write only. The app retains at most the three newest migration snapshots after an attempt that created a snapshot, whether that attempt commits or rolls back. The app verifies database integrity and foreign-key consistency before and after migration, then checkpoints WAL after a successful upgrade. A post-commit WAL checkpoint warning does not roll back a completed schema migration; diagnostics reports it separately for follow-up.

If a database was created by a newer app version, has an unknown migration ledger, or is not a recognized legacy schema, OpsMineFlow fails closed. It does not create tables, overwrite the database, seed sample data, or attempt an automatic restore. Keep the original database and use the pre-upgrade snapshot for manual recovery with a compatible build. Clearing a project never removes workspace-level migration snapshots; backup retention and all-data deletion are separately defined lifecycle operations. Filesystem or Time Machine backups are outside the app's control.
