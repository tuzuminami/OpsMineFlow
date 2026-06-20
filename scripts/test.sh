#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ -x "$ROOT_DIR/.venv/bin/python" ]]; then
  PYTHON_BIN="$ROOT_DIR/.venv/bin/python"
else
  PYTHON_BIN=""
  for candidate in python3.11 python3.12 python3.13 python3.14 python3; do
    if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' >/dev/null 2>&1; then
      PYTHON_BIN="$candidate"
      break
    fi
  done
  [[ -n "$PYTHON_BIN" ]] || { echo "Python 3.11 or newer is required." >&2; exit 1; }
fi

export PYTHONPATH="$ROOT_DIR/services/mining-core/src:$ROOT_DIR/services/local-api/src:$ROOT_DIR/packages/drawio-exporter/src:${PYTHONPATH:-}"

"$PYTHON_BIN" - <<'PY'
from pathlib import Path
import tomllib

for pyproject in Path(".").glob("**/pyproject.toml"):
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    readme = data.get("project", {}).get("readme")
    if not isinstance(readme, str):
        continue
    package_dir = pyproject.parent.resolve()
    readme_path = (pyproject.parent / readme).resolve()
    if not readme_path.is_relative_to(package_dir):
        raise SystemExit(f"Package readme must stay inside {package_dir}: {readme}")
    if not readme_path.is_file():
        raise SystemExit(f"Package readme was not found: {readme_path}")
PY

if "$PYTHON_BIN" -c 'import pytest' >/dev/null 2>&1; then
  "$PYTHON_BIN" -m pytest services/mining-core/tests services/local-api/tests packages/drawio-exporter/tests
else
  echo "pytest is not installed; running unittest fallback."
  "$PYTHON_BIN" -m unittest discover -s services/mining-core/tests
  "$PYTHON_BIN" -m unittest discover -s services/local-api/tests
  "$PYTHON_BIN" -m unittest discover -s packages/drawio-exporter/tests
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
./scripts/smoke_lifecycle.sh
