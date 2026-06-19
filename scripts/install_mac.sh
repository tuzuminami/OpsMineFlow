#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

VENV_DIR="${OPSMINEFLOW_VENV_DIR:-.venv}"
PYTHON_BIN="${PYTHON_BIN:-python3}"

info() {
  printf '==> %s\n' "$1"
}

fail() {
  printf 'ERROR: %s\n' "$1" >&2
  exit 1
}

need_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    fail "$1 was not found. Install it first, then run ./scripts/install_mac.sh again."
  fi
}

info "Checking macOS local app prerequisites"
if [[ "$(uname -s)" != "Darwin" ]]; then
  printf 'WARNING: OpsMineFlow is currently targeted at macOS. Continuing for development use.\n' >&2
fi

need_command "$PYTHON_BIN"
need_command node
need_command npm

if command -v cargo >/dev/null 2>&1; then
  cargo --version
else
  printf 'WARNING: Rust cargo was not found. Browser WebUI can run, but Tauri packaging requires Rust.\n' >&2
fi

if [[ "$(uname -s)" == "Darwin" ]]; then
  if xcode-select -p >/dev/null 2>&1; then
    xcode-select -p
  else
    printf 'WARNING: Xcode Command Line Tools were not found. Tauri packaging may fail until they are installed.\n' >&2
  fi
fi

"$PYTHON_BIN" - <<'PY'
import sys

if sys.version_info < (3, 11):
    raise SystemExit("Python 3.11 or newer is required.")
PY

node -e 'const [major] = process.versions.node.split(".").map(Number); if (major < 20) { console.error("Node.js 20 or newer is required."); process.exit(1); }'

info "Creating Python virtual environment at ${VENV_DIR}"
"$PYTHON_BIN" -m venv "$VENV_DIR"

VENV_PYTHON="$ROOT_DIR/$VENV_DIR/bin/python"
VENV_PIP="$ROOT_DIR/$VENV_DIR/bin/pip"

info "Installing Python packages"
"$VENV_PYTHON" -m pip install --upgrade pip setuptools wheel
"$VENV_PIP" install -e "packages/drawio-exporter"
"$VENV_PIP" install -e "services/mining-core[dev]"
"$VENV_PIP" install -e "services/local-api[dev]"

info "Installing desktop dependencies"
npm --prefix apps/desktop install

info "Running install smoke checks"
"$VENV_PYTHON" - <<'PY'
import importlib

for module in ("opsmineflow_mining", "opsmineflow_drawio", "opsmineflow_api"):
    importlib.import_module(module)
PY
npm --prefix apps/desktop run lint

info "Install complete"
printf '\nStart OpsMineFlow with:\n  ./scripts/run_local.sh\n'
