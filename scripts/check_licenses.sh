#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

echo "Checking license policy guardrails..."

FAILED=0
APACHE_2_0_SHA256="cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30"
MATCH_FILE="$(mktemp "${TMPDIR:-/tmp}/opsmineflow_license_match.XXXXXX")"
trap 'rm -f "$MATCH_FILE"' EXIT

if [[ ! -f LICENSE ]]; then
  echo "LICENSE file is missing."
  FAILED=1
else
  LICENSE_SHA256="$(shasum -a 256 LICENSE | awk '{print $1}')"
  if [[ "$LICENSE_SHA256" != "$APACHE_2_0_SHA256" ]]; then
    echo "LICENSE must match the complete, unmodified Apache License 2.0 text."
    FAILED=1
  fi
fi

PROHIBITED_PACKAGES=(
  pm4py
  apromore
  activitywatch
  aw-client
  aw-server
  screenrpa
  rpa-uilogger
  openai
  anthropic
  google-generativeai
  azure-ai
  ollama
  sentry
  posthog
  amplitude
  segment
)

while IFS= read -r -d '' manifest; do
  for package in "${PROHIBITED_PACKAGES[@]}"; do
    if grep -E -n -i -- "(^|[\"' =_-])${package}([\"' <>=_-]|$)" "$manifest" >"$MATCH_FILE" 2>/dev/null; then
      echo "Prohibited dependency candidate '$package' found in $manifest"
      cat "$MATCH_FILE"
      FAILED=1
    fi
  done
done < <(find . -type f '(' -name 'package.json' -o -name 'pyproject.toml' -o -name 'requirements*.txt' -o -name 'Cargo.toml' ')' \
  -not -path '*/node_modules/*' \
  -not -path '*/.venv/*' \
  -not -path '*/venv/*' \
  -not -path './apps/desktop/src-tauri/target/*' -print0)

if ! grep -R -n -I -- "Apache-2.0" LICENSE README.md README.ja.md docs/licenses >/dev/null; then
  echo "Apache-2.0 license declaration was not found."
  FAILED=1
fi

if [[ "$FAILED" -ne 0 ]]; then
  echo "License policy check failed."
  exit 1
fi

echo "License policy check passed."
