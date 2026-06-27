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
- Restricted CORS.
- No external telemetry.
- No LLM integrations.
- Audit-friendly documentation.

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
