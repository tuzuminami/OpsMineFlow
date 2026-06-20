# macOS Packaging

OpsMineFlow is distributed as a local-only macOS desktop app. The packaging flow builds Tauri `.app` and `.dmg` artifacts, collects them under `dist/macos`, and writes SHA256 checksums.

Requirements include macOS Sonoma or newer, Node.js 20 or newer, and Rust 1.85 or newer.

## Build Command

```bash
./scripts/package_macos.sh
```

The script runs:

- `./scripts/test.sh`
- `./scripts/check_licenses.sh`
- `./scripts/check_no_external_network.sh`
- `npm --prefix apps/desktop run build`
- `npm --prefix apps/desktop run tauri -- build --bundles app,dmg`

Set `OPSMINEFLOW_SKIP_CHECKS=1` only for a local rebuild after the checks already passed in the same workspace.

## Output

Artifacts are copied to:

```text
dist/macos/
```

Expected files:

- `OpsMineFlow.app.zip`
- `OpsMineFlow_*.dmg`
- `SHA256SUMS.txt`

Verify checksums:

```bash
cd dist/macos
shasum -a 256 -c SHA256SUMS.txt
```

## Signing And Notarization Policy

Client or public distribution should use Apple Developer ID signing and Apple notarization. Do not present an unsigned build as production-ready for third-party client delivery.

Recommended release policy:

1. Build with `./scripts/package_macos.sh`.
2. Sign the `.app` with a Developer ID Application certificate.
3. Create or sign the `.dmg`.
4. Submit the signed artifact to Apple notarization.
5. Staple notarization.
6. Re-run checksum generation for final release artifacts.
7. Attach the final artifacts and `SHA256SUMS.txt` to the GitHub Release.

Unsigned internal testing:

- Prefer Control-click, then Open, from Finder.
- If macOS quarantine blocks an internal test build, remove quarantine only for that local artifact:

```bash
xattr -dr com.apple.quarantine /path/to/OpsMineFlow.app
```

Do not ask non-technical client users to run quarantine removal commands.

## Local-Only Verification

Before release, run:

```bash
./scripts/check_no_external_network.sh
./scripts/check_licenses.sh
```

Then start the packaged app with the local API and confirm the WebUI Diagnostics panel reports:

- API bound to `127.0.0.1`
- WebUI local status
- external network blocked by policy
- local network guardrail passed
- license guardrail passed
- LLM integration not supported

## Release Checklist

- Version updated in `apps/desktop/package.json`, `apps/desktop/src-tauri/Cargo.toml`, and `apps/desktop/src-tauri/tauri.conf.json`.
- `./scripts/package_macos.sh` passed.
- `.app.zip`, `.dmg`, and `SHA256SUMS.txt` exist in `dist/macos`.
- Checksums verified with `shasum -a 256 -c SHA256SUMS.txt`.
- Signing/notarization status recorded in the release notes.
- Unsigned builds are marked internal-only.
