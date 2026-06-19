#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

export PYTHONPATH="$ROOT_DIR/services/mining-core/src:$ROOT_DIR/services/local-api/src:$ROOT_DIR/packages/drawio-exporter/src:${PYTHONPATH:-}"

if command -v pytest >/dev/null 2>&1; then
  pytest services/mining-core/tests services/local-api/tests packages/drawio-exporter/tests
else
  echo "pytest is not installed; running unittest fallback."
  python3 -m unittest discover -s services/mining-core/tests
  python3 -m unittest discover -s services/local-api/tests
  python3 -m unittest discover -s packages/drawio-exporter/tests
fi

if [[ -f apps/desktop/package.json && -d apps/desktop/node_modules ]]; then
  npm --prefix apps/desktop test
fi

if [[ -f packages/event-schema/package.json && -d packages/event-schema/node_modules ]]; then
  npm --prefix packages/event-schema test
fi

if [[ -f packages/drawio-exporter/package.json && -d packages/drawio-exporter/node_modules ]]; then
  npm --prefix packages/drawio-exporter test
fi

./scripts/smoke_local.sh
