# OpsMineFlow

[日本語版 README](README.ja.md)

OpsMineFlow is a local-first task mining and process mining assistant for macOS consultants. It helps with As-Is discovery, work inventory, process visualization, bottleneck analysis, automation candidate discovery, and consulting report drafts without requiring commercial SaaS contracts.

CI status, required checks, and the repository protection procedure are documented in [CI / main branch quality gate](docs/operations/CI.md).

## Why OpsMineFlow

Consulting teams often need to understand real work before proposing BPR, RPA, system renewal, or operating model changes. OpsMineFlow focuses on consent-based, local-only analysis that can be explained to client security, legal, and business teams.

OpsMineFlow is not an employee monitoring tool. It avoids keystroke logging, screenshots, screen recording, microphone use, camera use, and hidden telemetry.

## Local-Only Policy

Runtime data stays on the user's Mac. The app and API are designed to use only localhost, local files, and Tauri internal channels. External APIs, telemetry, cloud analytics, remote update checks, and crash uploaders are out of scope.

## No LLM / No Cloud API Policy

OpsMineFlow does not integrate with external LLM APIs, local LLMs, or AI agent frameworks. Labeling, mining, scoring, and reporting are rule-based and statistical.

## License

The project is licensed under Apache-2.0. Direct dependencies must be commercial-friendly and compatible with client delivery. AGPL, GPL, LGPL, SSPL, Commons Clause, Business Source License, Polyform, and non-commercial licenses are prohibited for core dependencies.

## Features

- CSV event log import
- JSON event log import, including ActivityWatch-style exports
- Optional ActivityWatch localhost import, enabled only by explicit user action
- Explicit start/stop recording of frontmost macOS applications
- Standard event schema
- URL and window-title masking
- Rule-based business labeling
- App usage and business-label duration analysis
- Directly-Follows Graph generation
- Variant analysis
- Bottleneck candidate detection
- Repeated pattern and app-switching analysis
- Automation candidate scoring
- Markdown, JSON, CSV, Mermaid, SVG, and draw.io-oriented exports

## Local Product Scope

OpsMineFlow targets a product-ready local workflow: one-command installation, one-command local startup, browser-based control, persistent local storage, diagnostics, recording, import, analysis, and export. Native macOS recording runs only after an explicit WebUI start. Browser extension logging remains a default-off roadmap item documented in [docs/product/COLLECTION_ROADMAP.md](docs/product/COLLECTION_ROADMAP.md).

## Quick Start

Requirements:

- macOS Sonoma or newer
- Python 3.11 or newer
- Node.js 20 or newer
- npm

From a fresh Mac terminal, install once:

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/tuzuminami/OpsMineFlow/main/scripts/bootstrap_mac.sh)"
```

If the repository is already cloned at the default location, reinstall with:

```bash
cd ~/OpsMineFlow && ./scripts/install_mac.sh
```

Start each time:

```bash
cd ~/OpsMineFlow && ./scripts/run_local.sh
```

Stop from another terminal:

```bash
cd ~/OpsMineFlow && ./scripts/stop_local.sh
```

You can also press `Control-C` in the terminal running OpsMineFlow. Re-running the start command safely reuses an existing healthy instance. `./scripts/...` commands work only from the OpsMineFlow repository directory. The bootstrap installs to `~/OpsMineFlow` by default; if you chose another location, use that actual directory instead.

The browser opens automatically at `http://127.0.0.1:5173`. Normal use after startup is completed in the WebUI.

## Beginner Guide

### 1. Choose English or Japanese

Use the **日本語 / English** control in the top-right corner. The first launch follows the browser language. Your choice is stored only in the browser and is restored on the next launch.

### 2. Understand the First Seven Events

The seven events shown on the first launch are sample data. They demonstrate the dashboard and are not records from your Mac.

- **Reload** reads the latest state from the local SQLite database. It does not initialize or delete data.
- **Delete sample data** or **Settings > Delete Data** removes events, labels, reviews, and import history.
- After deletion, the empty state remains empty after a reload or app restart.
- Privacy settings remain after data deletion.

To restore the example later, import `data/sample/sample_events.csv` from the repository.

### 3. Record Work on This Mac

Use **Record work** at the top of Home to capture a normal work session:

1. Enter a recognizable **Case or work unit**, such as `2026-06-21 Monthly invoice review`.
2. Enter the **Work label**, such as `Invoice processing`.
3. Read the collection scope and select the explicit consent checkbox.
4. Choose **Start recording**. If first-run sample data is still present, confirm its removal before recording begins.
5. Use Safari, Excel, mail, and other work applications normally. The panel shows the current app, elapsed time, and completed app intervals.
6. Choose **Stop recording** when that work unit is complete.
7. Review the result in **Dashboard**, **Process Map**, and **App Switching**.

Recording stores only the frontmost app display name, bundle identifier, start/end timestamps, and duration. It does not capture window titles, URLs, keystrokes, typed text, passwords, clipboard contents, screenshots, screen recordings, microphone audio, or camera video. Opening the WebUI does not start recording, and a stopped session never resumes by itself.

To omit an application, add its display name under **Settings > Excluded apps** before starting. If the recording agent is unavailable, reinstall it and restart OpsMineFlow:

```bash
cd ~/OpsMineFlow && ./scripts/install_mac.sh
```

For existing logs, choose **Home > Start collecting data** and use CSV/JSON or the explicit ActivityWatch localhost import.

### 4. Import a CSV or JSON File

1. Prepare a CSV or JSON event log.
2. In Finder, select the file and press `Option-Command-C` to copy its full pathname.
3. Open **Home > Data import**.
4. Choose CSV or JSON and paste the pathname.
5. Choose **Preview** and verify the event count, confidential flags, applications, and durations.
6. Choose **Import Previewed File** only after the preview looks correct.

CSV commonly uses `case_id`, `activity`, `timestamp_start`, `timestamp_end`, `user`, `app_name`, `url`, and `memo`. The import replaces the current analysis dataset and records the import in local history.

### 5. Analyze the Work

- **Dashboard**: totals, application time, business-label time, bottlenecks, and top automation candidates.
- **Event Explorer**: masked event-level records.
- **Process Map**: starts, ends, transitions, frequency, duration, and selected activity details.
- **App Switching**: application transitions and round trips.
- **Automation**: sort candidates and save Adopt, Hold, Reject, or Unreviewed states.
- **Reports**: review the locally generated Markdown report.

### 6. Export Results

Open **Home > Exports**, choose Markdown, JSON, CSV, Mermaid, or draw.io, and preview it. Review masking and confidential flags before choosing **Save to Path** or **Download**.

### 7. Check or Remove Local Data

Use **Home > Diagnostics** for API, WebUI, SQLite storage, dependencies, ports, ActivityWatch, and local-only policy. Use **Settings > Delete Data** when the current analysis must be removed. This deletion cannot be undone unless the source file still exists and is imported again.

### 8. Stop OpsMineFlow

Press `Control-C` in the startup terminal, or run this from another terminal:

```bash
cd ~/OpsMineFlow && ./scripts/stop_local.sh
```

For the full operating flow, see [docs/operations/RUNBOOK.md](docs/operations/RUNBOOK.md). For problems, see [docs/operations/TROUBLESHOOTING.md](docs/operations/TROUBLESHOOTING.md).

Build macOS app artifacts:

```bash
./scripts/package_macos.sh
```

Packaging details: [docs/operations/PACKAGING_MACOS.md](docs/operations/PACKAGING_MACOS.md)

## Developer Workflow

Run all checks:

```bash
./scripts/test.sh
./scripts/lint.sh
./scripts/check_licenses.sh
./scripts/check_no_external_network.sh
./scripts/smoke_local.sh
```

Run the local API and desktop UI during development:

```bash
./scripts/dev.sh
```

## CSV/JSON Reference

CSV imports support columns such as:

- `case_id`
- `activity`
- `timestamp_start`
- `timestamp_end`
- `user`
- `app_name`
- `url`
- `memo`

JSON imports normalize generic arrays and ActivityWatch-style exports into the OpsMineFlow standard event schema.

## Export Mermaid/SVG/draw.io

Process maps can be exported as Mermaid and draw.io-compatible mxfile XML. The WebUI can preview export content, download it, or save it to a local path. SVG export is planned as a local rendering step with no external CDN.

## Local Storage

Runtime data is stored in a local SQLite database under the user's application data directory by default. Set `OPSMINEFLOW_DATA_DIR` to use a different local directory.

## Privacy and Security

OpsMineFlow does not collect passwords, keystrokes, input text, screenshots, video, audio, or camera data. The standard workflow uses imported event logs and masking before analysis. Exports include a privacy warning and should be reviewed before sharing with clients.

## Disclaimer

OpsMineFlow provides local analysis support for consulting and operational improvement. It does not replace legal, HR, security, or compliance review.
