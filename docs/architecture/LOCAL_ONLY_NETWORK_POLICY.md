# Local-Only Network Policy

OpsMineFlow runtime components must not connect to external networks.

Allowed runtime targets:

- `http://127.0.0.1`
- `http://localhost`
- Tauri internal channels
- `file://`

Forbidden runtime behavior:

- External APIs.
- External LLMs.
- Local LLM integrations.
- Telemetry.
- Analytics.
- Remote crash reporting.
- Remote update checks.
- CDN scripts.
- External fonts.
- External images.

Dependency installation may require network access during development, but application runtime must remain local-only.

`scripts/check_no_external_network.sh` checks code and configuration for external URLs and prohibited integrations.

