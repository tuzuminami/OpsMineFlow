# Privacy and Security

OpsMineFlow is designed for consent-based business improvement analysis.

## Privacy Controls

- URL path masking.
- Window-title masking.
- Domain-only setting.
- Excluded apps.
- Excluded domains.
- Excluded keywords.
- Local deletion.
- Retention settings.
- Anonymous user IDs.
- Export preview and warning.

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

## Runtime Privacy Evidence

The local API exposes `GET /diagnostics` with a `privacy_evidence` section. It is intended for user-facing and client-facing checks that the runtime recorder is limited to `frontmost_app_only`.

Current evidence categories:

- `keystrokes`: no keyboard hooks, input monitoring APIs, or key event capture.
- `typed_text`: no form values, document text, clipboard contents, or page body text.
- `window_titles`: native recording stores an empty `window_title`.
- `urls`: native recording stores an empty URL; CSV/JSON imports still pass through masking.
- `screenshots`: no screenshot or screen-recording API in runtime collectors.
- `audio_camera`: no microphone or camera API in runtime collectors.
- `remote_reporting`: remote event reporting, analytics, crash upload, and update checks remain prohibited.

The WebUI Diagnostics panel renders the same evidence. `scripts/smoke_lifecycle.sh` and API tests assert that the evidence reports `not_collected` for every prohibited category.
