#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
"$SCRIPT_DIR/load_image.sh"
exec "$SCRIPT_DIR/start.sh"
