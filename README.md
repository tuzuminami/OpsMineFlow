# OpsMineFlow

[日本語版 README](README.ja.md)

OpsMineFlow is a local-first task mining and process mining assistant for macOS consultants. It helps with As-Is discovery, work inventory, process visualization, bottleneck analysis, automation candidate discovery, and consulting report drafts without requiring commercial SaaS contracts.

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

OpsMineFlow now targets a product-ready local workflow: one-command installation, one-command local startup, browser-based control, persistent local storage, diagnostics, import, analysis, and export. Native macOS logging, browser extension logging, and advanced swimlanes remain roadmap items.

## Quick Start

Requirements:

- macOS Sonoma or newer
- Python 3.11 or newer
- Node.js 20 or newer
- npm

Install once:

```bash
./scripts/install_mac.sh
```

One-line bootstrap from a fresh Mac terminal:

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/tuzuminami/OpsMineFlow/main/scripts/bootstrap_mac.sh)"
```

Start each time:

```bash
./scripts/run_local.sh
```

The browser opens automatically. Normal use after startup is completed in the WebUI.

## WebUI Workflow

### 1. Import Logs

Open **Home > Import**, select CSV or JSON, enter the local file path, and choose **Preview**. Confirm the event count and masked sample, then choose **Import Previewed File**. ActivityWatch localhost import stays disabled unless the user explicitly enables it.

### 2. Analyze Work

Use **Dashboard** for totals, **Event Explorer** for masked records, **Process Map** for transitions and bottlenecks, **App Switching** for handoff patterns, and **Automation** to sort candidates and save Adopt, Hold, or Reject review states.

### 3. Export Results

Open **Home > Exports**, choose Markdown, JSON, CSV, Mermaid, or draw.io, then preview the content. Save it to a local path or download it only after reviewing the privacy warning.

### 4. Run Diagnostics

Open **Home > Diagnostics** to inspect API, WebUI, storage, dependencies, ports, ActivityWatch, and local-only policy status. Choose **Run Checks** for the license and local-network guardrails.

### 5. Delete Local Analysis Data

Open **Settings**, review the local data controls, and choose **Delete Data**. Confirm the deletion prompt. Imported events, labels, review state, and import history are removed from the local database.

### 6. Stop

Return to the terminal running OpsMineFlow and press `Control-C`.

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

## Import CSV/JSON

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
