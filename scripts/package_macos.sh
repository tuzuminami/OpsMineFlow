#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DESKTOP_DIR="$ROOT_DIR/apps/desktop"
TAURI_DIR="$DESKTOP_DIR/src-tauri"
BUNDLE_DIR="$TAURI_DIR/target/release/bundle"
RELEASE_DIR="$ROOT_DIR/dist/macos"

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "macOS packaging must run on macOS."
  exit 1
fi

for command in npm cargo rustc shasum ditto; do
  if ! command -v "$command" >/dev/null 2>&1; then
    echo "Missing required command: $command"
    echo "Run ./scripts/install_mac.sh, then retry."
    exit 1
  fi
done

rust_version="$(rustc --version | awk '{print $2}')"
IFS='.' read -r rust_major rust_minor _ <<<"$rust_version"
if (( rust_major < 1 || (rust_major == 1 && rust_minor < 85) )); then
  echo "Rust 1.85 or newer is required for Tauri packaging. Found $rust_version."
  echo "Update Rust, then retry ./scripts/package_macos.sh."
  exit 1
fi

if [[ "${OPSMINEFLOW_SKIP_CHECKS:-0}" != "1" ]]; then
  "$ROOT_DIR/scripts/test.sh"
  "$ROOT_DIR/scripts/check_licenses.sh"
  "$ROOT_DIR/scripts/check_no_external_network.sh"
fi

npm --prefix "$DESKTOP_DIR" run build
npm --prefix "$DESKTOP_DIR" run tauri -- build --bundles app,dmg

rm -rf "$RELEASE_DIR"
mkdir -p "$RELEASE_DIR"

find "$BUNDLE_DIR" -type d -name '*.app' -print0 | while IFS= read -r -d '' app_path; do
  app_name="$(basename "$app_path")"
  ditto -c -k --keepParent "$app_path" "$RELEASE_DIR/${app_name}.zip"
done

find "$BUNDLE_DIR" -type f -name '*.dmg' -print0 | while IFS= read -r -d '' dmg_path; do
  cp "$dmg_path" "$RELEASE_DIR/"
done

if ! find "$RELEASE_DIR" -type f \( -name '*.zip' -o -name '*.dmg' \) | grep -q .; then
  echo "No .app zip or .dmg artifacts were produced."
  exit 1
fi

(
  cd "$RELEASE_DIR"
  artifacts=()
  while IFS= read -r artifact; do
    artifacts+=("$artifact")
  done < <(find . -type f \( -name '*.zip' -o -name '*.dmg' \) | sort)
  shasum -a 256 "${artifacts[@]}" >SHA256SUMS.txt
)

echo "macOS artifacts:"
find "$RELEASE_DIR" -type f -print | sort
