#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
# shellcheck disable=SC1091
source "$ROOT_DIR/scripts/lib.sh"
mode=${1:-}
shift || true
load_mode_config "$mode"

timestamp=$(date -u +%Y%m%dT%H%M%SZ)
results_dir="$ROOT_DIR/benchmarks/results"
mkdir -p "$results_dir" "$ROOT_DIR/run"
gpu_csv="$results_dir/gpu-${MODE}-${timestamp}.csv"
stop_file="$ROOT_DIR/run/metrics-${MODE}-${timestamp}.stop"
rm -f "$stop_file"

python3 "$ROOT_DIR/scripts/collect_metrics.py" \
  --output "$gpu_csv" --interval "${GPU_METRICS_INTERVAL:-1}" --stop-file "$stop_file" &
collector_pid=$!
cleanup() {
  : >"$stop_file"
  wait "$collector_pid" 2>/dev/null || true
  rm -f "$stop_file"
}
trap cleanup EXIT INT TERM

matrix_args=(
  --prompt-lengths "${PROMPT_LENGTHS:-1024,11000}"
  --output-lengths "${OUTPUT_LENGTHS:-128,512}"
  --concurrency "${CONCURRENCY_LEVELS:-1,2}"
  --repeats "${BENCHMARK_REPEATS:-1}"
)
if [[ "${BENCHMARK_MATRIX:-quick}" == full ]]; then
  matrix_args=(
    --prompt-lengths "1024,11000,32768,131072${INCLUDE_256K:+,262144}"
    --output-lengths "128,512,1024,2048"
    --concurrency "1,2,4,8"
    --repeats "${BENCHMARK_REPEATS:-1}"
  )
fi
baseline_args=()
if [[ -n "${BASELINE_PROMPT_FILE:-}" ]]; then
  baseline_args=(--baseline-prompt-file "$BASELINE_PROMPT_FILE")
fi

set +e
python3 "$ROOT_DIR/scripts/benchmark_api.py" \
  --base-url "http://$HOST:$PORT" \
  --model "$SERVED_MODEL_NAME" \
  --mode "$MODE" \
  --output-dir "$results_dir" \
  "${matrix_args[@]}" "${baseline_args[@]}" "$@"
benchmark_rc=$?
set -e

cleanup
trap - EXIT INT TERM
"$ROOT_DIR/scripts/mode_action.sh" "$MODE" logs --tail 100000 >/dev/null
latest_json=$(find "$results_dir" -maxdepth 1 -type f -name "benchmark-${MODE}-*.json" -printf '%T@ %p\n' | sort -nr | head -n1 | cut -d' ' -f2-)
[[ -n "$latest_json" ]] || die "benchmark JSON was not created"
python3 "$ROOT_DIR/scripts/finalize_benchmark.py" \
  --benchmark-json "$latest_json" \
  --gpu-csv "$gpu_csv" \
  --runtime-log "$ROOT_DIR/logs/$MODE/latest.log" \
  --startup-file "$ROOT_DIR/run/$MODE.startup.env"

printf 'GPU_METRICS=%s\n' "$gpu_csv"
exit "$benchmark_rc"
