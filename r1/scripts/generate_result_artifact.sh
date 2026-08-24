#!/usr/bin/env bash
set -euo pipefail

# Generate the canonical report artifact and a directly viewable portable HTML
# on the target without host Python.  A workstation builder remains available
# for the final Chromium-verified publication pass.
# shellcheck disable=SC1091
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

ensure_runtime_dirs
init_docker
matrix=${1:-$RESULT_DIR/performance-$SCHEME_ID/performance-matrix.csv}
[[ -f "$matrix" ]] || die "performance matrix is missing: $matrix"
matrix=$(cd -- "$(dirname -- "$matrix")" && pwd)/$(basename -- "$matrix")
matrix_dir=$(dirname -- "$matrix")
matrix_name=$(basename -- "$matrix")
output_name=performance-report.artifact.json

docker_cmd image inspect "$R1_IMAGE" >/dev/null 2>&1 || die \
  "release image is missing: $R1_IMAGE"
docker_cmd run --rm --network none \
  --user "$(id -u):$(id -g)" \
  --volume "$R1_DIR:/audit:ro" \
  --volume "$matrix_dir:/results:rw" \
  --entrypoint python3 "$R1_IMAGE" \
  /audit/reports/generate_artifact.py \
  --matrix "/results/$matrix_name" \
  --output "/results/$output_name" \
  --source-path "target-results/$SCHEME_ID/$matrix_name" \
  --scheme "$SCHEME_ID" \
  --scheme-label "$SCHEME_LABEL" \
  --gpu-devices "$GPU_DEVICES" \
  --tensor-parallel-size "$TENSOR_PARALLEL_SIZE" \
  --scheduler-max-seqs "$MAX_NUM_SEQS" \
  --benchmark-max-concurrency "$BENCHMARK_MAX_CONCURRENCY" \
  --theoretical-256k-concurrency "$THEORETICAL_256K_CONCURRENCY"

docker_cmd run --rm --network none \
  --user "$(id -u):$(id -g)" \
  --volume "$R1_DIR:/audit:ro" \
  --volume "$matrix_dir:/results:rw" \
  --entrypoint python3 "$R1_IMAGE" \
  /audit/reports/package_portable_report.py \
  --artifact "/results/$output_name" \
  --template /audit/reports/performance-report.html \
  --output /results/performance-report.html

printf 'REPORT_ARTIFACT=PASS\nartifact=%s/%s\nhtml=%s/performance-report.html\n' \
  "$matrix_dir" "$output_name" "$matrix_dir"
