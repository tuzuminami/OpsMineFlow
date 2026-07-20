# Runbook

## User Workflow

### 1. Install Once

Download the signed `OpsMineFlow_*.dmg` from [GitHub Releases](https://github.com/tuzuminami/OpsMineFlow/releases), then drag **OpsMineFlow.app** to Applications.

### 2. Start

Open **OpsMineFlow.app** from Applications. The app starts its managed local runtime and presents its own window; it does not require a browser URL or Terminal session.

### 3. Stop

Quit **OpsMineFlow.app**. It stops only the local runtime it owns and leaves unrelated processes untouched.

### 4. Choose a Project

Use the always-visible project selector to choose the client, engagement, or workstream before recording, importing, analysing, or exporting. Create a project with a clear display name when starting a new engagement. Project selection is disabled while a recording session is active so that one session cannot cross project boundaries.

The selected project is an explicit data boundary: its events, labels, privacy settings, import history, automation reviews, reports, and exports do not include another project's records. A migrated pre-project database appears as **Migrated data**; rename it after confirming its contents.

### 5. Record a Work Session

1. Open **Home > Record work**.
2. Enter a case or work-unit name and a work label.
3. Read the collection scope and select the explicit consent checkbox.
4. Choose **Start recording**.
5. Use the required Mac applications normally.
6. Choose **Stop recording** when the work unit is complete.

The recorder stores only frontmost application names, bundle identifiers, timestamps, and durations. It does not collect window titles, URLs, input text, screenshots, audio, or camera data. Use **Settings > Excluded apps** before starting when an application must be omitted.

### 6. Import Logs

1. Open **Home > Import**.
2. Choose CSV or JSON.
3. Choose the file in Finder.
4. Choose **Preview**.
5. Confirm the event count, confidential count, and masked sample.
6. Choose **Import Previewed File**.

ActivityWatch import is optional. Enable it only when the participant-approved scope includes ActivityWatch localhost data. If it is unavailable, use a CSV or JSON export instead.

### 7. Analyze

- **Dashboard**: totals, durations, and top signals
- **Event Explorer**: masked event-level records
- **Process Map**: activities, transitions, frequency, start/end counts, and bottlenecks
- **App Switching**: app transitions and round trips
- **Automation**: sortable candidates with Adopt, Hold, Reject, and Unreviewed states
- **Reports**: local Markdown report preview

Automation review states are stored in the local SQLite database and included in Markdown and JSON exports.

### 8. Export

1. Open **Home > Exports**.
2. Choose Markdown, JSON, CSV, Mermaid, draw.io, or **LLM handoff (ZIP)**.
3. Choose **Preview**.
4. Review masked fields, confidential flags, and the privacy warning.
5. Choose **Save** and select the destination in Finder.

Treat export preview as the final manual checkpoint before sharing output with a client.

### Manual Mermaid handoff

`LLM handoff (ZIP)` creates a versioned, deterministic local ZIP for manual sharing with an external LLM. It contains aggregate process evidence, public JSON Schemas, and fixed Mermaid-writing constraints; it does not make any LLM, cloud, or network call. The export omits raw event rows, IDs, URLs, titles, aliases, metadata, and review notes. Activity labels and app names remain event-derived data and must be reviewed before sharing. See [the sample contract](../samples/LLM_MERMAID_HANDOFF.md).

### 9. Clear or Delete a Project

1. Open **Settings**.
2. Choose **Delete Data**.
3. Confirm the deletion prompt.

This removes imported events, manual labels, automation reviews, and import history from the currently selected project only. The other projects in the local workspace remain unchanged. Privacy settings remain available for the next import into that project.

To remove a project itself, first clear its event data, then use the project selector's delete action. OpsMineFlow keeps at least one project available and does not permit deletion while that project has an active recording session.

## Diagnostics

Open **Home > Diagnostics** to check:

- API bind and port
- WebUI status and port
- SQLite path and record counts
- macOS recording agent availability
- Python, Node.js, npm, Cargo, and macOS versions
- ActivityWatch status when explicitly enabled
- local-only runtime policy

Choose **Run Checks** to execute the local license and network guardrails. The checks do not contact external diagnostic services.

## Local Data

The default SQLite database is stored under the user's macOS application data directory. One database can hold several explicitly isolated projects. Set `OPSMINEFLOW_DATA_DIR` before startup only when a separate local workspace is required.

Exports are written only to the local path chosen by the user.

### Database Upgrades and Recovery

At startup, OpsMineFlow checks the local SQLite schema before loading records. When an upgrade is needed, it creates a private pre-upgrade snapshot under the local data directory's `backups/` folder and runs the ordered migration transaction. The three newest migration snapshots are retained. Diagnostics reports the schema version, migration status, integrity status, and whether a backup was created; it never exposes the backup path in the UI.

If startup reports that the database is from a newer app version, unknown, or failed to migrate, stop using that database. Do not delete or overwrite it. Preserve the database and its `backups/` folder, then open the snapshot only with a compatible OpsMineFlow build or follow the support/recovery procedure documented for that release. Clearing a project does not remove workspace-level migration snapshots. It cannot erase operating-system or Time Machine backups.

## Problem Resolution

Use [TROUBLESHOOTING.md](TROUBLESHOOTING.md) for startup, port, dependency, recording, import, ActivityWatch, export, and packaging problems.

## Developer Workflow

Run all checks:

```bash
./scripts/test.sh
./scripts/lint.sh
./scripts/check_licenses.sh
./scripts/check_no_external_network.sh
./scripts/perf_smoke.sh
```

`./scripts/perf_smoke.sh` generates 1k, 10k, and 100k-event CSV/JSON datasets. It protects bounded event paging, analysis, real localhost API import and dashboard responses, staged CSV/JSON exports, and rendering of the 500-row Event Explorer page. The time limits are intentionally generous regression guards for supported Macs, not a performance promise for every workload.

`./scripts/lint.sh` runs `./scripts/check_migrations.sh`. The migration registry check rejects gaps and checksum changes to applied migrations, so schema changes must be introduced as a new migration.

Start development servers:

```bash
./scripts/dev.sh
```

Build macOS release artifacts:

```bash
./scripts/package_macos.sh
```

Run the Tauri desktop shell with its explicitly owned development sidecar:

```bash
./scripts/dev_desktop.sh
```

The development command is intentionally separate from `npm run tauri -- dev`: it passes the local Python interpreter and source import paths only to the Rust-owned child process. A packaged app never falls back to a repository checkout, Terminal, Node.js, or a system Python. It starts only the bundled local runtime after integrity verification, and fails closed with a recovery action instead of starting an arbitrary executable.

In the packaged app, the WebUI does not call `127.0.0.1` directly. It sends named, allowlisted operations to the Rust runtime, which holds the per-launch local API secret. Do not add a browser-side API secret, a direct production `fetch`, or a generic URL/method proxy. `./scripts/dev.sh` and `./scripts/run_local.sh` are browser-only helpers that require an explicit insecure-development opt-in and must only use disposable test data.

For CSV/JSON import, choose the source through Finder. For saved exports, choose the destination through Finder. Do not ask users to type local paths; the desktop runtime holds a short-lived selection scope and rechecks the selected item before use.

If the desktop app asks to repair prior runtime state, first make sure no other OpsMineFlow process is running. Then choose **Repair local runtime state** and confirm the safety prompt. OpsMineFlow keeps the unverified ownership record in a private quarantine location and starts a replacement only after confirming that the local port is free. Do not delete runtime ownership records manually.

See [PACKAGING_MACOS.md](PACKAGING_MACOS.md) before client or public distribution.
