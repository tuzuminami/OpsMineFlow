#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

VENV_DIR="${OPSMINEFLOW_VENV_DIR:-.venv}"
PYTHON_BIN="${PYTHON_BIN:-}"

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

python_is_supported() {
  "$1" - <<'PY' >/dev/null 2>&1
import sys
raise SystemExit(0 if sys.version_info >= (3, 11) else 1)
PY
}

info "Checking macOS local app prerequisites"
if [[ "$(uname -s)" != "Darwin" ]]; then
  printf 'WARNING: OpsMineFlow is currently targeted at macOS. Continuing for development use.\n' >&2
fi

if [[ -n "$PYTHON_BIN" ]]; then
  need_command "$PYTHON_BIN"
  python_is_supported "$PYTHON_BIN" || fail "$PYTHON_BIN must be Python 3.11 or newer."
else
  for candidate in python3.11 python3.12 python3.13 python3.14 python3; do
    if command -v "$candidate" >/dev/null 2>&1 && python_is_supported "$candidate"; then
      PYTHON_BIN="$candidate"
      break
    fi
  done
  [[ -n "$PYTHON_BIN" ]] || fail "Python 3.11 or newer was not found. Install it, then rerun this command."
fi

info "Using $($PYTHON_BIN --version 2>&1) from $(command -v "$PYTHON_BIN")"
need_command node
need_command npm

if command -v cargo >/dev/null 2>&1; then
  cargo --version
  rust_version="$(rustc --version | awk '{print $2}')"
  IFS='.' read -r rust_major rust_minor _ <<<"$rust_version"
  if (( rust_major < 1 || (rust_major == 1 && rust_minor < 85) )); then
    printf 'WARNING: Rust 1.85 or newer is required for Tauri packaging. Browser WebUI installation can continue.\n' >&2
  fi
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
printf '\nStart OpsMineFlow with:\n  cd "%s" && ./scripts/run_local.sh\n' "$ROOT_DIR"
