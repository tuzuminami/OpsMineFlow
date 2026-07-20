# Privacy and Security

OpsMineFlow is designed for consent-based business improvement analysis.

## Privacy Controls

- Mandatory event-data minimization before an imported or recorded event enters the in-memory store or SQLite.
- Project-scoped opaque case, source-event, and event references; original source identifiers are not retained.
- Empty persisted/API fields for user alias, app bundle ID, window title, URL, URL mask, and freeform memo.
- A strict metadata allowlist for process provenance and review status; unknown metadata is dropped.
- Optional normalized domain host for excluded-domain filtering; URL path and query are never retained.
- Excluded apps.
- Excluded domains.
- Local deletion.
- Retention settings.
- All API, report, and export formats use the same safe event profile. A legacy masking setting cannot re-enable raw data.
- Exports fail closed when an event is marked confidential.

## Security Controls

- Local files only.
- Localhost-only API bind.
- A fresh, 256-bit local API session secret for every desktop runtime; it is held only by the Rust runtime and the local API process.
- The packaged WebUI uses an allowlisted Tauri command rather than browser `fetch` to localhost. It cannot select an arbitrary HTTP method, path, or header.
- Exact localhost Host validation, production Origin rejection, bounded JSON request bodies, and development-only CORS.
- Minimal unauthenticated readiness endpoints only; user data, diagnostics, reports, and exports require the desktop runtime session.
- One-time, short-lived delete challenges that remain inside the Rust runtime.
- Explicit opaque project context on every data operation. The Rust runtime validates the UUID, carries it in a fixed local header, and the API rejects a missing context; a remembered project selection never authorizes a data read or write.
- No external telemetry.
- No LLM integrations.
- Audit-friendly documentation.

## Local API Trust Boundary

The packaged desktop UI never receives the local API session secret and never sends a direct request to the loopback API. A Rust-owned sidecar launcher creates the secret for one runtime, passes it to the local API, and removes it from inherited child environments. Before a request carries that secret, Rust verifies the sidecar on the same TCP connection with a per-runtime HMAC probe; a listener that cannot prove ownership receives no API session secret. The Rust command layer maps each permitted UI action to a fixed local route and applies time and response-size limits.

For project-scoped routes, the Rust layer also removes the UI-supplied `project_id` from the JSON body after validating its canonical opaque UUID form and sends the value only as `X-OpsMineFlow-Project`. The API binds all data queries and mutations to that context and uses a revision compare-and-swap check to reject stale writes. The active-project preference in SQLite is never consulted to infer a request's data scope.

`GET /health` and `GET /runtime/health` are intentionally public so the launcher can safely determine whether it owns a previous local sidecar. Their public response is minimal operational metadata and does not read SQLite or disclose event counts. The runtime ownership nonce, PID, and HMAC proof appear only when Rust supplies a fresh probe challenge. All WebUI product routes require the per-runtime secret in production. Recorder ingestion uses its separate, explicit recording-session control instead of the WebUI secret. Browser-based development requires an explicit insecure-development opt-in and is not a packaged-product access path.

Deletion requires a second, single-use challenge. The API issues and consumes that challenge atomically; the challenge is never returned to JavaScript in the packaged desktop app.

## Data Not Collected

- Keystrokes.
- Passwords.
- Input text.
- Continuous clipboard text.
- Screenshots.
- Screen recordings.
- Audio.
- Camera images.

## Event Data Boundary

Schema version 4 applies the safe event profile to both existing and new data.
It removes raw alias, title, URL, memo, app bundle ID, freeform review note,
and unknown metadata from SQLite. It also turns case, source, and event
identifiers into project-scoped HMAC references backed by a local owner-only
key. The same profile is applied to in-memory
events before API responses, reports, CSV/JSON/Markdown/Mermaid/draw.io
exports, and the manual Mermaid handoff bundle are created.

Activity labels and application names remain because they are the minimum
evidence needed to describe a process. They are treated as data, never as
instructions, in the handoff bundle. A user must review labels and the
confidential flag before sharing an export.

When a pre-v4 database is upgraded, the rewrite runs in one SQLite transaction
and does not create a plaintext pre-upgrade snapshot. A failure leaves the
original database untouched; a successful migration leaves only the minimized
database. Encrypted backup, retention, and all-data-deletion lifecycle work
remain tracked separately by #52, #53, and #54.

An existing database stores a non-secret verifier for its local pseudonym key.
If the owner-only key file is missing or does not match, OpsMineFlow fails
closed instead of silently generating a new key and breaking ID consistency.

## Runtime Privacy Evidence

The local API exposes `GET /diagnostics` with a `privacy_evidence` section. It is intended for user-facing and client-facing checks that the runtime recorder is limited to `frontmost_app_only`.

Current evidence categories:

- `keystrokes`: no keyboard hooks, input monitoring APIs, or key event capture.
- `typed_text`: no form values, document text, clipboard contents, or page body text.
- `window_titles`: native recording stores an empty `window_title`.
- `urls`: native recording stores an empty URL; CSV/JSON and ActivityWatch imports discard raw URLs and retain at most a normalized host for exclusion filtering.
- `screenshots`: no screenshot or screen-recording API in runtime collectors.
- `audio_camera`: no microphone or camera API in runtime collectors.
- `remote_reporting`: remote event reporting, analytics, crash upload, and update checks remain prohibited.

The WebUI Diagnostics panel renders the same evidence. `scripts/smoke_lifecycle.sh` and API tests assert that the evidence reports `not_collected` for every prohibited category.
