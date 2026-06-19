# macOS Install

## Requirements

- macOS Sonoma or newer.
- Python 3.11 or newer.
- Node.js 20 or newer.
- npm.

## Install

```bash
./scripts/install_mac.sh
```

The installer creates `.venv`, installs Python packages, installs desktop dependencies, and runs smoke checks.

Fresh-machine bootstrap:

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/tuzuminami/OpsMineFlow/main/scripts/bootstrap_mac.sh)"
```

## Run

```bash
./scripts/run_local.sh
```

The app runs on localhost only. Docker is not required.
