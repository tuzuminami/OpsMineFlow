#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

echo "Checking local development tools..."
python3 --version
node --version
npm --version

echo "Optional: create a virtual environment with Python 3.11+"
echo "  python3 -m venv .venv"
echo "  source .venv/bin/activate"

echo "Install Python packages when network is available:"
echo "  python -m pip install -e services/mining-core[dev]"
echo "  python -m pip install -e services/local-api[dev]"

echo "Install desktop dependencies when network is available:"
echo "  npm --prefix apps/desktop install"

echo "Setup check complete."

