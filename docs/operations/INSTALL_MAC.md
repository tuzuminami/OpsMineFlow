# macOS Install

## For Users

Requirements:

- macOS Sonoma or newer

1. Download the signed `OpsMineFlow_*.dmg` from [GitHub Releases](https://github.com/tuzuminami/OpsMineFlow/releases).
2. Open the disk image and drag **OpsMineFlow.app** to Applications.
3. Open **OpsMineFlow.app** from Applications.

The desktop app owns its local runtime and starts it only after validation. No Terminal, Python, Node.js, browser URL, cloud account, API key, or LLM is required for normal use.

Docker, a cloud account, an API key, and an LLM are not required.

Do not use source checkout scripts for client or participant data. `./scripts/run_local.sh` is an intentionally insecure browser-development helper, protected by the explicit `OPSMINEFLOW_INSECURE_BROWSER_DEV_API=1` opt-in.

## First Run Check

After startup:

1. Confirm the desktop window opens.
2. Open **Home > Diagnostics**.
3. Confirm the API bind is `127.0.0.1`.
4. Confirm external network is blocked by policy.
5. Choose **Run Checks** and confirm both guardrails pass.

If startup fails, use [TROUBLESHOOTING.md](TROUBLESHOOTING.md).

## For Developers

Python 3.11 or newer, Node.js 20 or newer, and Rust 1.85 or newer are required for source development. Run `./scripts/dev_desktop.sh` for a managed local desktop runtime. The browser-only helper may be used only with disposable test data:

```bash
OPSMINEFLOW_INSECURE_BROWSER_DEV_API=1 ./scripts/run_local.sh
```

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
