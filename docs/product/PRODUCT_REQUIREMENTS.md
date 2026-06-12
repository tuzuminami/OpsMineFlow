# Product Requirements

## Product

OpsMineFlow is a local-first macOS OSS tool for task mining and process mining support in operational improvement consulting.

## Goals

- Import consent-based work event logs from CSV and JSON.
- Normalize events into a standard local schema.
- Analyze app usage, business labels, transitions, variants, bottlenecks, repeated work, app switching, and automation candidates.
- Export consulting-ready artifacts without cloud services.
- Keep privacy, security, and license posture explainable to clients.

## Non-Monitoring Position

OpsMineFlow is not an employee monitoring tool. It does not collect keystrokes, passwords, screenshots, screen recordings, microphone input, camera input, or hidden telemetry.

## Functional Requirements

- CSV import.
- JSON import.
- Optional ActivityWatch localhost import.
- Rule-based labels and manual labels.
- Local process mining without PM4Py.
- Mermaid and draw.io export.
- Markdown report generation.
- Local API bound to 127.0.0.1.
- Desktop UI showing dashboard, events, process map, app switching, candidates, reports, and settings.

## Quality Requirements

- Apache-2.0 project license.
- Commercial-friendly dependencies only.
- External runtime network checks.
- License checks.
- Test coverage for import, mining, and draw.io export.

