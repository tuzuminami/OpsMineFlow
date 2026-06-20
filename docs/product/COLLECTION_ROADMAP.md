# Collection Roadmap

OpsMineFlow remains import-first by default. Native background collection and browser collection are separate opt-in capabilities, not prerequisites for the local product.

## Decision: Native Agent

The first native macOS agent will be Swift-only.

Reasons:

- one signed and notarized technology stack
- smaller permission and process surface
- no bundled Python, shell, or third-party helper daemon
- easier explanation to client security teams
- direct use of macOS lifecycle, menu bar, and permission APIs

A separate helper process is deferred. It may be reconsidered only if measured reliability requirements cannot be met in the signed Swift app. Any helper proposal requires a new threat model, signing plan, uninstall path, and legal/security review.

### Native Agent Scope

Allowed after explicit start:

- frontmost application bundle identifier and display name
- active window title only after masking rules are applied
- start/end timestamps and duration
- user-defined exclusions
- visible running/paused state

Not collected:

- keystrokes, passwords, input text, or form values
- clipboard contents
- screenshots or screen recordings
- document contents
- microphone, camera, or audio
- hidden background activity

The first release must work without Screen Recording, microphone, or camera permissions. Accessibility permission may be requested only when window metadata cannot be obtained through a lower-permission API, and the UI must explain why before macOS displays the permission prompt.

## Decision: Browser Extension

The browser extension will be a separate, optional installation. It will use a Manifest V3-style permission model where supported and request host access only for user-approved domains.

Allowed permissions and data:

- `activeTab` for explicit current-tab interaction
- local extension storage for allowlists and masking settings
- optional host permissions for approved domains
- connection to `127.0.0.1` only while OpsMineFlow is running
- browser name, domain, masked URL path, timestamps, and duration
- page title only when separately enabled and masked

Prohibited permissions and data:

- no `<all_urls>` default permission
- no page body or DOM text capture
- no form values, input events, passwords, or clipboard data
- no cookies, authentication headers, web request bodies, or session tokens
- no browsing history import
- no downloads, screenshots, screen recording, microphone, or camera
- no remote endpoint, remote configuration, or update check implemented by OpsMineFlow

The extension must show a persistent enabled/paused state and provide one-click pause and clear-local-buffer actions.

## Local Data Path

Collectors must send only normalized, masked events to the local OpsMineFlow boundary. Direct collector writes to the SQLite database are not allowed because they would bypass validation, retention, and deletion controls.

Preferred paths:

1. Tauri internal command or app-managed local channel for the bundled Swift agent.
2. `127.0.0.1` ingestion endpoint with restricted CORS and an app-generated local session token for a browser extension.
3. Local file import as the fallback when live local ingestion is unavailable.

No collector may contact a cloud service.

## Release Order

1. **Current local product**: CSV/JSON and explicit ActivityWatch localhost import, WebUI analysis, SQLite, diagnostics, and exports.
2. **Native agent technical preview**: Swift-only, manual start/stop, visible status, app metadata first, no browser extension dependency.
3. **Native agent controlled beta**: signed/notarized build, consent flow, exclusion controls, retention/deletion verification, and client security review.
4. **Browser extension technical preview**: separate install, optional domain permissions, local-only transport, and no page-content capture.
5. **Browser extension controlled beta**: browser-store/enterprise policy review, consent updates, and end-to-end deletion verification.

Native collection and browser collection remain out of the default production release until their review gates pass.

## Required Review Gates

- product owner approval of collection purpose and default-off behavior
- legal approval of consent and participant notice text
- client security review of permissions, localhost transport, storage, retention, and deletion
- privacy review of URL/title masking and domain/app exclusions
- signed/notarized macOS distribution plan
- browser store or enterprise deployment policy review
- test evidence that prohibited data is never captured
- uninstall and local-data deletion verification
