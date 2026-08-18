#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
# shellcheck disable=SC1091
source "$ROOT_DIR/scripts/lib.sh"
load_mode_config target-only
output="$ROOT_DIR/benchmarks/results/smoke-target-only-$(date -u +%Y%m%dT%H%M%SZ).json"
args=(--base-url "http://$HOST:$PORT" --model "$SERVED_MODEL_NAME" --mode "$MODE" --output "$output")
[[ "${STRICT_FEATURES:-0}" == 1 ]] && args+=(--strict-features)
exec python3 "$ROOT_DIR/scripts/api_smoke_test.py" "${args[@]}" "$@"
