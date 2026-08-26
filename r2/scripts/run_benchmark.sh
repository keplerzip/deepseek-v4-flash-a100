#!/usr/bin/env bash
set -euo pipefail

# shellcheck disable=SC1091
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

"$R2_DIR/scripts/load_image.sh"
if container_running "$CONTAINER_NAME"; then
  assert_owned_container "$CONTAINER_NAME"
  http_ready || die 'selected service is running but not API-ready'
else
  "$R2_DIR/scripts/start.sh"
fi

result_subdir="$RESULT_DIR/benchmark"
mkdir -p "$result_subdir"
benchmark_basename=${DSV4_BENCHMARK_BASENAME:-long-context-matrix}
[[ "$benchmark_basename" =~ ^[a-zA-Z0-9._-]+$ ]] || die \
  'DSV4_BENCHMARK_BASENAME contains unsafe characters'
csv_path="$result_subdir/$benchmark_basename.csv"
container_args=(
  --rm
  --network bridge
  --add-host host.docker.internal:host-gateway
  --user "$(id -u):$(id -g)"
  --volume "$R2_DIR:/r2:ro"
  --volume "$result_subdir:/results:rw"
  --entrypoint python3
)
if [[ -n "${DSV4_API_KEY:-}" ]]; then
  container_args+=(--env "DSV4_API_KEY=$DSV4_API_KEY")
fi
docker_cmd run "${container_args[@]}" "$R2_IMAGE" \
  /r2/benchmarks/long_context_matrix.py \
  --scheme "$SCHEME_ID" \
  --dspark-k "${DSPARK_K:-7}" \
  --cache-profile "$PREFIX_CACHE_PROFILE" \
  --output "/results/$benchmark_basename.csv" \
  "$@"

docker_cmd run --rm --network none \
  --user "$(id -u):$(id -g)" \
  --volume "$R2_DIR:/r2:ro" \
  --volume "$result_subdir:/results:rw" \
  --entrypoint python3 "$R2_IMAGE" \
  /r2/reports/generate_report.py \
  "/results/$benchmark_basename.csv" "/results/$benchmark_basename.html"
printf 'BENCHMARK=PASS\nscheme=%s\ncsv=%s\nsummary=%s\nreport=%s\n' \
  "$SCHEME_ID" "$csv_path" \
  "$result_subdir/$benchmark_basename.summary.json" \
  "$result_subdir/$benchmark_basename.html"
