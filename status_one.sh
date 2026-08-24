#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
export DSV4_SCHEME=one
exec "$ROOT_DIR/r1/scripts/status.sh"
