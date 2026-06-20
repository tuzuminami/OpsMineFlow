# Troubleshooting

## Start Here

When the WebUI opens, use **Home > Diagnostics** first. Confirm the API, WebUI, storage, dependencies, and ports, then choose **Run Checks**.

When the WebUI does not open, rerun:

```bash
./scripts/run_local.sh
```

Read the first `ERROR:` line in the terminal. The startup script stops instead of silently choosing another port.

## Dependencies Are Missing

Run the installer again:

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

## API Does Not Start

The API must bind to `127.0.0.1:8765`. Check its health locally:

```bash
curl http://127.0.0.1:8765/health
```

If port 8765 is already in use:

```bash
lsof -nP -iTCP:8765 -sTCP:LISTEN
```

Stop the old OpsMineFlow process, then rerun `./scripts/run_local.sh`. Do not bind the API to `0.0.0.0`.

## WebUI Does Not Open

Open the local URL manually:

```text
http://127.0.0.1:5173
```

Check for a port conflict:

```bash
lsof -nP -iTCP:5173 -sTCP:LISTEN
```

Stop the stale process and rerun `./scripts/run_local.sh`.

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
