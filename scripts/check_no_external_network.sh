#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

echo "Checking local-only network policy..."

SCAN_PATHS=(apps services packages scripts)
URL_PATTERN='https?://[^"'"'"'` <>)]+'
FAILED=0

is_scan_metadata() {
  local file="$1"
  [[ "$file" == apps/desktop/src-tauri/target/* \
    || "$file" == apps/desktop/src-tauri/gen/* \
    || "$file" == apps/desktop/src-tauri/icons/* \
    || "$file" == *"Cargo.lock" \
    || "$file" == *"package-lock.json" ]]
}

source_files() {
  find "$@" -type f \
    -not -path '*/node_modules/*' \
    -not -path '*/.venv/*' \
    -not -path '*/venv/*' \
    -not -path '*/.pytest_cache/*' \
    -not -path '*/dist/*' \
    -not -path '*/src-tauri/target/*' \
    -not -path '*/src-tauri/gen/*' \
    -not -path '*/src-tauri/icons/*' -print0
}

while IFS= read -r -d '' file; do
  while IFS= read -r match; do
    value="${match#*:}"
    if [[ "$file" == "scripts/check_no_external_network.sh" || "$file" == "scripts/check_licenses.sh" || "$file" == "scripts/bootstrap_mac.sh" ]] || is_scan_metadata "$file"; then
      continue
    fi
    if [[ "$value" =~ ^https?://(127\.0\.0\.1|localhost)([:/].*)?$ ]]; then
      continue
    fi
    echo "External URL candidate: $file -> $value"
    FAILED=1
  done < <(grep -E -n -I -o -- "$URL_PATTERN" "$file" 2>/dev/null || true)
done < <(source_files "${SCAN_PATHS[@]}")

PROHIBITED_TERMS=(
  telemetry
  google-analytics
  analytics.js
  sentry
  posthog
  amplitude
  segment
  openai
  anthropic
  googleapis
  azure
  ollama
)

for term in "${PROHIBITED_TERMS[@]}"; do
  while IFS= read -r -d '' file; do
    while IFS= read -r match; do
      if [[ "$file" == "scripts/check_no_external_network.sh" || "$file" == "scripts/check_licenses.sh" || "$file" == "scripts/bootstrap_mac.sh" ]] || is_scan_metadata "$file"; then
        continue
      fi
      echo "Prohibited integration term '$term' found in $file:$match"
      FAILED=1
    done < <(grep -E -n -I -i -- "$term" "$file" 2>/dev/null || true)
  done < <(source_files "${SCAN_PATHS[@]}")
done

while IFS= read -r -d '' file; do
  if grep -E -n -I -- '"dangerousRemoteDomainIpcAccess"|externalBin|allowlist.*all|0\.0\.0\.0' "$file" 2>/dev/null; then
    echo "Potential unsafe network or Tauri configuration found in $file."
    FAILED=1
  fi
done < <(source_files apps packages services)

if [[ "$FAILED" -ne 0 ]]; then
  echo "Local-only network policy check failed."
  exit 1
fi

echo "Local-only network policy check passed."
