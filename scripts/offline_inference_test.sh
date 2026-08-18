#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
# shellcheck disable=SC1091
source "$ROOT_DIR/scripts/lib.sh"
mode=${1:-}
case "$mode" in target-only|dspark) ;; *) die "Usage: $0 {target-only|dspark}" ;; esac
load_mode_config "$mode"
detect_runtime

if container_exists "$CONTAINER_NAME" || [[ -s "$RUN_DIR/dsv4-a100.lock" ]]; then
  die "normal service/lock exists; stop it before the disconnected test"
fi

log "starting $mode with Docker/Podman network namespace disabled"
NETWORK_MODE=none EXECUTION_MODE=eager "$ROOT_DIR/$mode/start.sh"
cleanup() {
  NETWORK_MODE=none "$ROOT_DIR/$mode/stop.sh" || true
}
trap cleanup EXIT INT TERM

container_output="/runtime-tmp/offline-smoke.json"
runtime exec "$CONTAINER_NAME" python3 /bundle-scripts/api_smoke_test.py \
  --base-url "http://127.0.0.1:$PORT" \
  --model "$SERVED_MODEL_NAME" \
  --mode "$MODE" \
  --output "$container_output"

host_output="$RUN_DIR/tmp/$MODE/offline-smoke.json"
result_output="$ROOT_DIR/benchmarks/results/offline-smoke-${MODE}-$(date -u +%Y%m%dT%H%M%SZ).json"
cp "$host_output" "$result_output"
runtime logs "$CONTAINER_NAME" >"$LOG_ROOT/$MODE/offline-network-none.log" 2>&1 || true

if grep -Eiq 'https?://(huggingface\.co|modelscope|pypi\.org)|ConnectionError.*(huggingface|modelscope|pypi)' \
  "$LOG_ROOT/$MODE/offline-network-none.log"; then
  die "runtime log contains an attempted external model/package URL"
fi
printf 'OFFLINE_INFERENCE=PASS\nmode=%s\nresult=%s\n' "$MODE" "$result_output"
cleanup
trap - EXIT INT TERM
