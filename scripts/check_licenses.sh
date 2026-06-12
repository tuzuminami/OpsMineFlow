#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

echo "Checking license policy guardrails..."

FAILED=0

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

while IFS= read -r manifest; do
  for package in "${PROHIBITED_PACKAGES[@]}"; do
    if rg -n -i "(^|[\"' =_-])${package}([\"' <>=_-]|$)" "$manifest" >/tmp/opsmineflow_license_match.txt 2>/dev/null; then
      echo "Prohibited dependency candidate '$package' found in $manifest"
      cat /tmp/opsmineflow_license_match.txt
      FAILED=1
    fi
  done
done < <(find . -type f '(' -name 'package.json' -o -name 'pyproject.toml' -o -name 'requirements*.txt' ')' \
  -not -path './node_modules/*' \
  -not -path './.venv/*' \
  -not -path './venv/*' \
  -not -path './apps/desktop/src-tauri/target/*' | sort)

if ! rg -n "Apache-2.0" LICENSE README.md README.ja.md docs/licenses >/dev/null; then
  echo "Apache-2.0 license declaration was not found."
  FAILED=1
fi

if [[ "$FAILED" -ne 0 ]]; then
  echo "License policy check failed."
  exit 1
fi

echo "License policy check passed."
