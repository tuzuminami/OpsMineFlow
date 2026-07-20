# Troubleshooting

## Start Here

When the desktop app opens, use **Home > Diagnostics** first. Confirm the API, desktop runtime, storage, dependencies, and ports, then choose **Run Checks**.

If the desktop window does not open, quit OpsMineFlow, open it again from Applications, and review the recovery action shown by the app. Do not start the browser-development helper with client data.

## Dependencies Are Missing

For a packaged app, reinstall the signed disk image from GitHub Releases. For source development, run the installer again:

```bash
./scripts/install_mac.sh
```

Useful version checks:

```bash
python3 --version
node --version
npm --version
cargo --version
```

Python 3.11 or newer and Node.js 20 or newer are required. Rust 1.85 or newer is required only for Tauri packaging.

## API Does Not Start (Developer Diagnostics)

The API must bind to `127.0.0.1:8765`. Check its health locally:

```bash
curl http://127.0.0.1:8765/health
```

If port 8765 is already in use during source development:

```bash
lsof -nP -iTCP:8765 -sTCP:LISTEN
cd ~/OpsMineFlow && ./scripts/stop_local.sh
```

The stop script terminates the listener only when the health response identifies it as OpsMineFlow. Do not bind the API to `0.0.0.0`.

## Desktop Window Does Not Open

Open OpsMineFlow from Applications. If it presents a runtime recovery action, follow that action and do not manually delete runtime files. The managed runtime rejects unrelated listeners rather than reusing them.

## Mac Recording Is Unavailable

Open **Home > Diagnostics** and check **Mac recording agent**. If it is unavailable, rebuild the local Swift helper:

```bash
cd ~/OpsMineFlow && ./scripts/install_mac.sh
cd ~/OpsMineFlow && ./scripts/stop_local.sh
cd ~/OpsMineFlow && ./scripts/dev_desktop.sh
```

Recording starts only from **Home > Record work** after a case, work label, and explicit consent are supplied. Opening or reloading the WebUI does not start a session. If an active session appears stuck, choose **Stop recording** before restarting OpsMineFlow.

The recorder intentionally does not request Screen Recording, microphone, camera, or input-monitoring permissions. It records frontmost application names and durations only.

## Import Preview Fails

- Confirm the file path is local and the file exists.
- Confirm CSV or JSON matches the selected format.
- Preview before importing.
- For CSV, verify the header includes usable case, activity, and timestamp fields.
- For JSON, use a generic event array or an ActivityWatch-style export.

## ActivityWatch Is Not Reachable

ActivityWatch is optional and is checked only after explicit enablement. Confirm its local service is available on `127.0.0.1:5600`. If it is not running, import an exported CSV or JSON file instead.

Do not expose ActivityWatch or OpsMineFlow on a non-local interface.

## Diagnostics Check Fails

Run the failing command shown in the WebUI:

```bash
./scripts/check_licenses.sh
./scripts/check_no_external_network.sh
```

Remove the blocked dependency or external runtime integration, then rerun the check. Generated Tauri build output and lockfile registry metadata are excluded from runtime-source scanning.

## Export Cannot Be Saved

- Use a writable local path.
- Include a filename or choose an existing directory.
- Review the privacy warning before saving.
- For draw.io, confirm the file has a `.drawio` extension and contains `mxfile`, `diagram`, `mxGraphModel`, and `root`.

## Local Data Must Be Removed

Use **Settings > Delete Data** and confirm the prompt. This removes imported events, labels, automation reviews, and import history. Restart OpsMineFlow and confirm the event count remains zero.

## macOS Package Build Fails

Confirm:

```bash
node --version
rustc --version
cargo --version
xcode-select -p
```

Then rerun:

```bash
./scripts/package_macos.sh
```

See [PACKAGING_MACOS.md](PACKAGING_MACOS.md) for signing, notarization, checksums, and unsigned internal testing.
