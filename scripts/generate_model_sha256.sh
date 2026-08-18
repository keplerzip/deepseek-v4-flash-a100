#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
# shellcheck disable=SC1091
source "$ROOT_DIR/config/model.env"
OUTPUT=${1:-"$ROOT_DIR/checksums/MODEL_SHA256SUMS"}

printf 'Hashing a ~167 GB checkpoint can take a long time. Progress is resumable.\n'
exec python3 "$ROOT_DIR/scripts/verify_model.py" "$MODEL_DIR" --sha256-output "$OUTPUT"
