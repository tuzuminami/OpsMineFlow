# macOS Install

## For Users

Requirements:

- macOS Sonoma or newer
- Python 3.11 or newer
- Node.js 20 or newer
- npm

From a cloned repository, install once:

```bash
./scripts/install_mac.sh
```

The installer creates `.venv`, installs the local Python packages and WebUI dependencies, and runs install checks.

Start OpsMineFlow:

```bash
./scripts/run_local.sh
```

The browser opens automatically. Keep this terminal window open while using OpsMineFlow. Press `Control-C` in the terminal to stop the local API and WebUI.

Docker, a cloud account, an API key, and an LLM are not required.

## Fresh Mac Bootstrap

The bootstrap command clones the repository and runs the installer:

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/tuzuminami/OpsMineFlow/main/scripts/bootstrap_mac.sh)"
```

Review the downloaded bootstrap script before using it in a client-managed environment.

## First Run Check

After startup:

1. Confirm the browser opens the local WebUI.
2. Open **Home > Diagnostics**.
3. Confirm the API bind is `127.0.0.1`.
4. Confirm external network is blocked by policy.
5. Choose **Run Checks** and confirm both guardrails pass.

If startup fails, use [TROUBLESHOOTING.md](TROUBLESHOOTING.md).

## For Developers

Run the complete local checks:

```bash
./scripts/test.sh
./scripts/lint.sh
./scripts/check_licenses.sh
./scripts/check_no_external_network.sh
```

Run the development servers:

```bash
./scripts/dev.sh
```

## Package For macOS

Create `.app` and `.dmg` artifacts:

```bash
./scripts/package_macos.sh
```

See [PACKAGING_MACOS.md](PACKAGING_MACOS.md) for Rust requirements, signing, notarization, checksums, and unsigned internal testing.
