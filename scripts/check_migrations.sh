#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ -x "$ROOT_DIR/.venv/bin/python" ]]; then
  PYTHON_BIN="$ROOT_DIR/.venv/bin/python"
else
  PYTHON_BIN="python3"
fi

export PYTHONPATH="$ROOT_DIR/services/mining-core/src:$ROOT_DIR/services/local-api/src:$ROOT_DIR/packages/drawio-exporter/src:${PYTHONPATH:-}"

echo "Checking SQLite migration registry..."
"$PYTHON_BIN" - <<'PY'
from opsmineflow_api.migrations import CURRENT_SCHEMA_VERSION, validate_migration_registry

validate_migration_registry()
print(f"Migration registry is valid through schema version {CURRENT_SCHEMA_VERSION}.")
PY
