#!/usr/bin/env bash
set -euo pipefail

# shellcheck disable=SC1091
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

ensure_runtime_dirs
http_ready || die "API is not ready: $(api_url /v1/models)"
# A fixed per-scheme directory makes the matrix resumable after interruption.
# Set DSV4_PERFORMANCE_DIR to retain an additional independent run.
run_dir=${DSV4_PERFORMANCE_DIR:-$RESULT_DIR/performance-$SCHEME_ID}
mkdir -p "$run_dir"

arguments=(
  "$@"
  --base-url "$(api_url /v1)"
  --api-key "${DSV4_API_KEY:-}"
  --model "$SERVED_MODEL_NAME"
  --output /results/performance-matrix.csv
  --max-concurrency "$BENCHMARK_MAX_CONCURRENCY"
  --run-prefix "dsv4-r1-$SCHEME_ID"
)
host_user="$(id -u):$(id -g)"
docker_cmd run --rm --network host \
  --user "$host_user" \
  --volume "$R1_DIR:/audit:ro" \
  --volume "$run_dir:/results:rw" \
  --entrypoint python3 "$R1_IMAGE" \
  /audit/benchmarks/performance_matrix.py "${arguments[@]}" \
  2>&1 | tee -a "$run_dir/run.log"
printf 'PERFORMANCE_RESULT=%s\n' "$run_dir/performance-matrix.csv"
printf 'scheme=%s\ncells=%s\nmax_concurrency=%s\n' \
  "$SCHEME_ID" "$BENCHMARK_MATRIX_CELLS" "$BENCHMARK_MAX_CONCURRENCY"
printf 'Next: r1/reports/build_report.sh %q\n' \
  "$run_dir/performance-matrix.csv"
