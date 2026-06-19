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

## macOS Installation

Requirements:

- macOS Sonoma or newer
- Python 3.11 or newer
- Node.js 20 or newer
- npm

Install:

```bash
./scripts/install_mac.sh
```

Run:

```bash
./scripts/run_local.sh
```

## Development Setup

Run all checks:

```bash
./scripts/test.sh
./scripts/lint.sh
./scripts/check_licenses.sh
./scripts/check_no_external_network.sh
```

Run the local API and desktop UI during development:

```bash
./scripts/dev.sh
```

## Usage

1. Explain the collection scope to participants.
2. Obtain consent.
3. Start OpsMineFlow with `./scripts/run_local.sh`.
4. Import CSV, JSON, or explicitly enabled ActivityWatch localhost data from the WebUI.
5. Review events, diagnostics, process maps, app switching, and automation candidates.
6. Adjust local privacy settings if needed.
7. Export Mermaid, draw.io, Markdown, CSV, or JSON artifacts.

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

Process maps can be exported as Mermaid and draw.io-compatible mxfile XML. SVG export is planned as a local rendering step with no external CDN.

## Local Storage

Runtime data is stored in a local SQLite database under the user's application data directory by default. Set `OPSMINEFLOW_DATA_DIR` to use a different local directory.

## Privacy and Security

OpsMineFlow does not collect passwords, keystrokes, input text, screenshots, video, audio, or camera data. The standard workflow uses imported event logs and masking before analysis. Exports include a privacy warning and should be reviewed before sharing with clients.

## Disclaimer

OpsMineFlow provides local analysis support for consulting and operational improvement. It does not replace legal, HR, security, or compliance review.
