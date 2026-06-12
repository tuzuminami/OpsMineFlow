#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

echo "Checking local-only network policy..."

SCAN_PATHS=(apps services packages scripts)
URL_PATTERN='https?://[^"'"'"'` <>)]+'
FAILED=0

while IFS= read -r match; do
  file="${match%%:*}"
  rest="${match#*:}"
  value="${rest#*:}"
  if [[ "$file" == "scripts/check_no_external_network.sh" || "$file" == "scripts/check_licenses.sh" ]]; then
    continue
  fi
  if [[ "$value" =~ ^https?://(127\.0\.0\.1|localhost)([:/].*)?$ ]]; then
    continue
  fi
  echo "External URL candidate: $file -> $value"
  FAILED=1
done < <(rg -n -o "$URL_PATTERN" "${SCAN_PATHS[@]}" 2>/dev/null || true)

PROHIBITED_TERMS=(
  telemetry
  analytics
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
  while IFS= read -r match; do
    file="${match%%:*}"
    if [[ "$file" == "scripts/check_no_external_network.sh" || "$file" == "scripts/check_licenses.sh" ]]; then
      continue
    fi
    echo "Prohibited integration term '$term' found in $match"
    FAILED=1
  done < <(rg -n -i "$term" apps services packages scripts 2>/dev/null || true)
done

if rg -n '"dangerousRemoteDomainIpcAccess"|externalBin|allowlist.*all|0\.0\.0\.0' apps packages services 2>/dev/null; then
  echo "Potential unsafe network or Tauri configuration found."
  FAILED=1
fi

if [[ "$FAILED" -ne 0 ]]; then
  echo "Local-only network policy check failed."
  exit 1
fi

echo "Local-only network policy check passed."
