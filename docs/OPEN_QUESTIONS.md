# Open Questions

## Decisions Recorded

- Native macOS agent v1: Swift-only. A helper process requires a new threat model and approval.
- Persistent store: SQLite in the local macOS application data directory.
- Browser extension: separate opt-in installation with optional domain permissions and no default `<all_urls>` access.
- Collector transport: validated Tauri/local API boundary; collectors must not write directly to SQLite.
- Release order: native agent technical preview before browser extension technical preview.

See [product/COLLECTION_ROADMAP.md](product/COLLECTION_ROADMAP.md) for the full decision record.

## Legal Review Required

- Approve exact participant consent and collection notice text for native and browser collection.
- Confirm client responsibilities for employee representation, works council, labor, and jurisdiction-specific review.
- Define whether window titles, page titles, domains, and masked URL paths are permitted for each client engagement.
- Define retention periods, access roles, participant access requests, correction, and deletion procedures.
- Define the allowed use of collected data in consulting deliverables and prohibit employee scoring or disciplinary use.

## Security Review Required

- Confirm whether Accessibility permission is acceptable for window metadata and document the lower-permission alternatives considered.
- Approve localhost ingestion authentication, extension session-token handling, CORS, replay protection, and rate limits.
- Approve browser optional-host-permission wording and verify no default broad host access.
- Verify signed/notarized macOS distribution, helper prohibition, uninstall behavior, and local-data deletion.
- Verify prohibited data tests for keystrokes, form values, cookies, authentication data, page content, screenshots, audio, and camera.
- Decide the browser distribution path: official browser store, enterprise-managed deployment, or signed internal package.

## Product Questions

- Confirm whether SVG export should use a bundled local renderer or remain deferred while Mermaid and draw.io exports are available.
- Define the minimum evidence required to promote each collector from technical preview to controlled beta.
- Define the user-facing preview, confirmation, retention, and deletion receipt for project data versus workspace migration snapshots before the all-data lifecycle work is released (tracked by #52 and #54).
