#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
# shellcheck disable=SC1091
source "$ROOT_DIR/scripts/lib.sh"
mode=${1:-}
shift || true
load_mode_config "$mode"
timestamp=$(date -u +%Y%m%dT%H%M%SZ)
output="$ROOT_DIR/benchmarks/results/stability-${MODE}-${timestamp}.json"
gpu_csv="$ROOT_DIR/benchmarks/results/stability-gpu-${MODE}-${timestamp}.csv"
stop_file="$ROOT_DIR/run/stability-metrics-${MODE}-${timestamp}.stop"
rm -f "$stop_file"
python3 "$ROOT_DIR/scripts/collect_metrics.py" --output "$gpu_csv" --stop-file "$stop_file" &
collector_pid=$!
cleanup() {
  : >"$stop_file"
  wait "$collector_pid" 2>/dev/null || true
  rm -f "$stop_file"
}
trap cleanup EXIT INT TERM
set +e
python3 "$ROOT_DIR/scripts/stability_test.py" \
  --base-url "http://$HOST:$PORT" --model "$SERVED_MODEL_NAME" --mode "$MODE" \
  --minutes "${STABILITY_MINUTES:-10}" --concurrency "${STABILITY_CONCURRENCY:-2}" \
  --output "$output" "$@"
test_rc=$?
set -e
cleanup
trap - EXIT INT TERM
"$ROOT_DIR/scripts/mode_action.sh" "$MODE" logs --tail 100000 >/dev/null
python3 "$ROOT_DIR/scripts/finalize_benchmark.py" \
  --benchmark-json "$output" --gpu-csv "$gpu_csv" \
  --runtime-log "$ROOT_DIR/logs/$MODE/latest.log"
exit "$test_rc"
