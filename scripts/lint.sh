#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

echo "Compiling Python files..."
python3 -m compileall -q services packages/drawio-exporter/src

./scripts/check_migrations.sh

echo "Checking shell syntax..."
while IFS= read -r script; do
  bash -n "$script"
done < <(find scripts -type f -name '*.sh' | sort)

if [[ -f apps/desktop/package.json && -d apps/desktop/node_modules ]]; then
  npm --prefix apps/desktop run lint
fi

echo "Lint checks passed."
