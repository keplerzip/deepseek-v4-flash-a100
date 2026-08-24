#!/usr/bin/env bash
set -euo pipefail

R1_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
ROOT_DIR=$(cd -- "$R1_DIR/.." && pwd)
# shellcheck disable=SC1091
source "$R1_DIR/scripts/lib.sh"
default_matrix=$R1_DIR/reports/data/performance-matrix.csv
if [[ "$SCHEME_ID" == two ]]; then
  default_matrix=$R1_DIR/reports/data/performance-matrix-two.csv
fi
matrix=${1:-$default_matrix}
artifact="$R1_DIR/reports/$REPORT_BASENAME.artifact.json"
report="$R1_DIR/reports/$REPORT_BASENAME.html"

command -v node >/dev/null 2>&1 || {
  printf 'ERROR: workstation Node.js is required for HTML report packaging.\n' >&2
  exit 1
}

matrix=$(cd -- "$(dirname -- "$matrix")" && pwd)/$(basename -- "$matrix")
if [[ "$matrix" == "$ROOT_DIR/"* ]]; then
  source_path=${matrix#"$ROOT_DIR/"}
else
  source_path="target-results/$(basename -- "$matrix")"
fi

# Python remains inside Docker; the workstation only needs Docker and Node for
# the official browser verifier.
init_docker
report_image=$R1_IMAGE
if ! docker_cmd image inspect "$report_image" >/dev/null 2>&1; then
  report_image=$BASE_IMAGE
fi
docker_cmd image inspect "$report_image" >/dev/null 2>&1 || {
  printf 'ERROR: the R1 or fixed-base Docker image is required.\n' >&2
  exit 1
}
matrix_dir=$(dirname -- "$matrix")
matrix_name=$(basename -- "$matrix")
docker_cmd run --rm --network none \
  --user "$(id -u):$(id -g)" \
  --volume "$R1_DIR:/audit:ro" \
  --volume "$matrix_dir:/matrix:ro" \
  --volume "$R1_DIR/reports:/delivery:rw" \
  --entrypoint python3 "$report_image" \
  /audit/reports/generate_artifact.py \
  --matrix "/matrix/$matrix_name" \
  --output "/delivery/$REPORT_BASENAME.artifact.json" \
  --source-path "$source_path" \
  --scheme "$SCHEME_ID" \
  --scheme-label "$SCHEME_LABEL" \
  --gpu-devices "$GPU_DEVICES" \
  --tensor-parallel-size "$TENSOR_PARALLEL_SIZE" \
  --scheduler-max-seqs "$MAX_NUM_SEQS" \
  --benchmark-max-concurrency "$BENCHMARK_MAX_CONCURRENCY" \
  --theoretical-256k-concurrency "$THEORETICAL_256K_CONCURRENCY"

plugin_root=${DATA_ANALYTICS_PLUGIN_ROOT:-}
if [[ -z "$plugin_root" ]]; then
  printf 'ERROR: DATA_ANALYTICS_PLUGIN_ROOT must point to the data-analytics plugin.\n' >&2
  printf 'The canonical artifact was generated at %s; HTML was not replaced.\n' \
    "$artifact" >&2
  exit 1
fi
builder="$plugin_root/skills/build-report/scripts/build_portable_artifact.mjs"
verifier="$plugin_root/skills/build-report/scripts/verify_portable_artifact.mjs"
[[ -f "$builder" && -f "$verifier" ]] || {
  printf 'ERROR: portable report builder/verifier not found below: %s\n' \
    "$plugin_root" >&2
  exit 1
}
mkdir -p "$R1_DIR/reports/qa"
candidate=$(mktemp "$R1_DIR/reports/.$REPORT_BASENAME.XXXXXX.html")
candidate_name=$(basename -- "$candidate")
cleanup() {
  rm -f -- "$candidate"
}
trap cleanup EXIT
node "$builder" \
  --input "$artifact" \
  --output "$candidate"
docker_cmd run --rm --network none \
  --user "$(id -u):$(id -g)" \
  --volume "$R1_DIR:/audit:ro" \
  --volume "$R1_DIR/reports:/delivery:rw" \
  --entrypoint python3 "$report_image" \
  /audit/reports/harden_portable_report.py \
  --input "/delivery/$candidate_name" --output "/delivery/$candidate_name"
node "$verifier" \
  --artifact "$artifact" \
  --html "$candidate" \
  --screenshot "$R1_DIR/reports/qa/report-failure.png" \
  | tee "$R1_DIR/reports/qa/delivery-receipt-$SCHEME_ID.json"
mv -f -- "$candidate" "$report"
trap - EXIT
printf 'REPORT_BUILD=PASS\nartifact=%s\nhtml=%s\n' "$artifact" "$report"
