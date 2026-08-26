#!/usr/bin/env bash
set -euo pipefail
export DSV4_SCHEME=target
# shellcheck disable=SC1091
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib.sh"
"$R2_DIR/scripts/load_image.sh"
comparison_dir="$RUNTIME_BASE/comparison"
mkdir -p "$comparison_dir"
docker_cmd run --rm --network none \
  --user "$(id -u):$(id -g)" \
  --volume "$R2_DIR:/r2:ro" \
  --volume "$RUNTIME_BASE:/runtime:rw" \
  --entrypoint python3 "$R2_IMAGE" \
  /r2/benchmarks/compare_schemes.py \
  /runtime/target/results/benchmark/long-context-matrix.csv \
  /runtime/dspark-k7/results/benchmark/long-context-matrix.csv \
  --output /runtime/comparison/target-vs-dspark-k7.json
printf 'SCHEME_COMPARISON=PASS\noutput=%s\n' \
  "$comparison_dir/target-vs-dspark-k7.json"
