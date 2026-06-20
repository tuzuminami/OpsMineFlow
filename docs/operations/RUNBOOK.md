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

### 4. Import Logs

1. Open **Home > Import**.
2. Choose CSV or JSON.
3. Enter the local file path.
4. Choose **Preview**.
5. Confirm the event count, confidential count, and masked sample.
6. Choose **Import Previewed File**.

ActivityWatch import is optional. Enable it only when the participant-approved scope includes ActivityWatch localhost data. If it is unavailable, use a CSV or JSON export instead.

### 5. Analyze

- **Dashboard**: totals, durations, and top signals
- **Event Explorer**: masked event-level records
- **Process Map**: activities, transitions, frequency, start/end counts, and bottlenecks
- **App Switching**: app transitions and round trips
- **Automation**: sortable candidates with Adopt, Hold, Reject, and Unreviewed states
- **Reports**: local Markdown report preview

Automation review states are stored in the local SQLite database and included in Markdown and JSON exports.

### 6. Export

1. Open **Home > Exports**.
2. Choose Markdown, JSON, CSV, Mermaid, or draw.io.
3. Enter a local save path or use browser download.
4. Choose **Preview**.
5. Review masked fields, confidential flags, and the privacy warning.
6. Save or download the artifact.

Treat export preview as the final manual checkpoint before sharing output with a client.

### 7. Delete Local Analysis Data

1. Open **Settings**.
2. Choose **Delete Data**.
3. Confirm the deletion prompt.

This removes imported events, manual labels, automation reviews, and import history from the local SQLite database. Privacy settings remain available for the next import.

## Diagnostics

Open **Home > Diagnostics** to check:

- API bind and port
- WebUI status and port
- SQLite path and record counts
- Python, Node.js, npm, Cargo, and macOS versions
- ActivityWatch status when explicitly enabled
- local-only runtime policy

Choose **Run Checks** to execute the local license and network guardrails. The checks do not contact external diagnostic services.

## Local Data

The default SQLite database is stored under the user's macOS application data directory. Set `OPSMINEFLOW_DATA_DIR` before startup only when a separate local workspace is required.

Exports are written only to the local path chosen by the user.

## Problem Resolution

Use [TROUBLESHOOTING.md](TROUBLESHOOTING.md) for startup, port, dependency, import, ActivityWatch, export, and packaging problems.

## Developer Workflow

Run all checks:

```bash
./scripts/test.sh
./scripts/lint.sh
./scripts/check_licenses.sh
./scripts/check_no_external_network.sh
```

Start development servers:

```bash
./scripts/dev.sh
```

Build macOS release artifacts:

```bash
./scripts/package_macos.sh
```

See [PACKAGING_MACOS.md](PACKAGING_MACOS.md) before client or public distribution.
