#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

echo "setup_mac.sh is kept for compatibility. Running the product installer."
exec "$ROOT_DIR/scripts/install_mac.sh"
