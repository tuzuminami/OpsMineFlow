#!/usr/bin/env bash
set -euo pipefail

INSTALL_DIR="${OPSMINEFLOW_INSTALL_DIR:-$HOME/OpsMineFlow}"
REPO_URL="${OPSMINEFLOW_REPO_URL:-https://github.com/tuzuminami/OpsMineFlow.git}"

info() {
  printf '==> %s\n' "$1"
}

fail() {
  printf 'ERROR: %s\n' "$1" >&2
  exit 1
}

if ! command -v git >/dev/null 2>&1; then
  fail "git was not found. Install Xcode Command Line Tools or Git first."
fi

if [[ -d "$INSTALL_DIR/.git" ]]; then
  info "Updating existing checkout at ${INSTALL_DIR}"
  git -C "$INSTALL_DIR" pull --ff-only
else
  info "Cloning OpsMineFlow into ${INSTALL_DIR}"
  git clone "$REPO_URL" "$INSTALL_DIR"
fi

cd "$INSTALL_DIR"
./scripts/install_mac.sh

printf '\nOpsMineFlow is installed at:\n  %s\n' "$INSTALL_DIR"
printf 'Start it with:\n  cd "%s" && ./scripts/run_local.sh\n' "$INSTALL_DIR"
