#!/usr/bin/env bash
set -euo pipefail

# A focused A/B/C gate: three contexts × three requested hit rates, C16, short
# decode. The selected profile must then pass the full 60-cell long benchmark
# and a 24-hour stability run before becoming final.
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
R2_DIR=$(cd -- "$SCRIPT_DIR/.." && pwd)
export DSV4_SCHEME=target
cleanup_profile_service() {
  DSV4_SCHEME=target "$SCRIPT_DIR/stop.sh" >/dev/null 2>&1 || true
}
trap cleanup_profile_service EXIT

for profile in legacy zero 32768; do
  export PREFIX_CACHE_PROFILE=$profile
  # shellcheck disable=SC1091
  source "$SCRIPT_DIR/lib.sh"
  "$SCRIPT_DIR/stop.sh"
  "$SCRIPT_DIR/install_and_start.sh"
  DSV4_BENCHMARK_BASENAME="cache-profile-$profile" \
    "$SCRIPT_DIR/run_benchmark.sh" \
      --contexts 200000,600000,1000000 \
      --outputs 256 \
      --hit-rates 0.80,0.90,0.95 \
      --concurrency 16 \
      --overwrite
done

export PREFIX_CACHE_PROFILE=zero
# shellcheck disable=SC1091
source "$SCRIPT_DIR/lib.sh"
decision_dir="$RUNTIME_BASE/cache-profile-decision"
mkdir -p "$decision_dir"
docker_cmd run --rm --network none \
  --user "$(id -u):$(id -g)" \
  --volume "$R2_DIR:/r2:ro" \
  --volume "$RUNTIME_BASE:/runtime:rw" \
  --entrypoint python3 "$R2_IMAGE" \
  /r2/benchmarks/select_cache_profile.py \
  --legacy /runtime/target/results/benchmark/cache-profile-legacy.csv \
  --zero /runtime/target/results/benchmark/cache-profile-zero.csv \
  --retention-32768 /runtime/target/results/benchmark/cache-profile-32768.csv \
  --output /runtime/cache-profile-decision/decision.json
cleanup_profile_service
trap - EXIT
printf 'CACHE_PROFILE_GATE=PASS\ndecision=%s\n' "$decision_dir/decision.json"
