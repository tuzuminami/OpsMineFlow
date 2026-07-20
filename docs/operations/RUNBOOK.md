# Runbook

## User Workflow

Commands beginning with `./scripts/` must be run from the OpsMineFlow repository directory. The default bootstrap location is `~/OpsMineFlow`.

### 1. Install Once

```bash
./scripts/install_mac.sh
```

### 2. Start

```bash
cd ~/OpsMineFlow && ./scripts/run_local.sh
```

The command starts the API and WebUI on localhost and opens the browser. Keep the terminal open. Press `Control-C` to stop both services.

If OpsMineFlow is already healthy, running the same command opens it without treating its ports as an error.

### 3. Stop

From another terminal:

```bash
cd ~/OpsMineFlow && ./scripts/stop_local.sh
```

The stop script verifies that the listeners belong to OpsMineFlow before terminating them. It leaves unrelated programs untouched.

### 4. Record a Work Session

1. Open **Home > Record work**.
2. Enter a case or work-unit name and a work label.
3. Read the collection scope and select the explicit consent checkbox.
4. Choose **Start recording**.
5. Use the required Mac applications normally.
6. Choose **Stop recording** when the work unit is complete.

The recorder stores only frontmost application names, bundle identifiers, timestamps, and durations. It does not collect window titles, URLs, input text, screenshots, audio, or camera data. Use **Settings > Excluded apps** before starting when an application must be omitted.

### 5. Import Logs

1. Open **Home > Import**.
2. Choose CSV or JSON.
3. Enter the local file path.
4. Choose **Preview**.
5. Confirm the event count, confidential count, and masked sample.
6. Choose **Import Previewed File**.

ActivityWatch import is optional. Enable it only when the participant-approved scope includes ActivityWatch localhost data. If it is unavailable, use a CSV or JSON export instead.

### 6. Analyze

- **Dashboard**: totals, durations, and top signals
- **Event Explorer**: masked event-level records
- **Process Map**: activities, transitions, frequency, start/end counts, and bottlenecks
- **App Switching**: app transitions and round trips
- **Automation**: sortable candidates with Adopt, Hold, Reject, and Unreviewed states
- **Reports**: local Markdown report preview

Automation review states are stored in the local SQLite database and included in Markdown and JSON exports.

### 7. Export

1. Open **Home > Exports**.
2. Choose Markdown, JSON, CSV, Mermaid, or draw.io.
3. Enter a local save path or use browser download.
4. Choose **Preview**.
5. Review masked fields, confidential flags, and the privacy warning.
6. Save or download the artifact.

Treat export preview as the final manual checkpoint before sharing output with a client.

### 8. Delete Local Analysis Data

1. Open **Settings**.
2. Choose **Delete Data**.
3. Confirm the deletion prompt.

This removes imported events, manual labels, automation reviews, and import history from the local SQLite database. Privacy settings remain available for the next import.

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

The default SQLite database is stored under the user's macOS application data directory. Set `OPSMINEFLOW_DATA_DIR` before startup only when a separate local workspace is required.

Exports are written only to the local path chosen by the user.

### Database Upgrades and Recovery

At startup, OpsMineFlow checks the local SQLite schema before loading records. When an upgrade is needed, it creates a private pre-upgrade snapshot under the local data directory's `backups/` folder and runs the ordered migration transaction. The three newest migration snapshots are retained. Diagnostics reports the schema version, migration status, integrity status, and whether a backup was created; it never exposes the backup path in the UI.

If startup reports that the database is from a newer app version, unknown, or failed to migrate, stop using that database. Do not delete or overwrite it. Preserve the database and its `backups/` folder, then open the snapshot only with a compatible OpsMineFlow build or follow the support/recovery procedure documented for that release. **Delete Data** removes both active analysis records and migration snapshots, but it cannot erase operating-system or Time Machine backups.

## Problem Resolution

Use [TROUBLESHOOTING.md](TROUBLESHOOTING.md) for startup, port, dependency, recording, import, ActivityWatch, export, and packaging problems.

## Developer Workflow

Run all checks:

```bash
./scripts/test.sh
./scripts/lint.sh
./scripts/check_licenses.sh
./scripts/check_no_external_network.sh
```

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

The development command is intentionally separate from `npm run tauri -- dev`: it passes the local Python interpreter and source import paths only to the Rust-owned child process. A packaged app never falls back to a repository checkout, Terminal, Node.js, or a system Python. Until #78 bundles the signed local runtime, a packaged build fails closed with a recovery action instead of starting an arbitrary executable.

If the desktop app asks to repair prior runtime state, first make sure no other OpsMineFlow process is running. Then choose **Repair local runtime state** and confirm the safety prompt. OpsMineFlow keeps the unverified ownership record in a private quarantine location and starts a replacement only after confirming that the local port is free. Do not delete runtime ownership records manually.

See [PACKAGING_MACOS.md](PACKAGING_MACOS.md) before client or public distribution.
