#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
R2_DIR=$(cd -- "$SCRIPT_DIR/.." && pwd)
export DSV4_SCHEME=dspark
export PREFIX_CACHE_PROFILE=${PREFIX_CACHE_PROFILE:-zero}
sweep_root=${RUNTIME_BASE:-/var/tmp/dsv4-a100-r2.1-20260830}/dspark-k-sweep
mkdir -p "$sweep_root"
status_file="$sweep_root/status.tsv"
printf 'k\tstartup\tbenchmark\tevidence\n' >"$status_file"

for k in 1 3 5 7; do
  export DSV4_DSPARK_K=$k
  start_log="$sweep_root/k${k}-startup.log"
  benchmark_log="$sweep_root/k${k}-benchmark.log"
  "$SCRIPT_DIR/stop.sh" >"$sweep_root/k${k}-stop.log" 2>&1 || true
  set +e
  "$SCRIPT_DIR/install_and_start.sh" >"$start_log" 2>&1
  start_status=$?
  set -e
  if ((start_status != 0)); then
    printf '%s\tfailed\tnot_run\t%s\n' "$k" "$start_log" >>"$status_file"
    continue
  fi

  set +e
  DSV4_BENCHMARK_BASENAME="dspark-k${k}-screen" \
    "$SCRIPT_DIR/run_benchmark.sh" \
      --contexts 200000,600000,1000000 \
      --outputs 10000,20000,30000 \
      --hit-rates 0.90 \
      --concurrency 16 \
      --overwrite >"$benchmark_log" 2>&1
  benchmark_status=$?
  set -e
  if ((benchmark_status == 0)); then
    printf '%s\tpass\tpass\t%s\n' "$k" "$benchmark_log" >>"$status_file"
  else
    printf '%s\tpass\tfailed\t%s\n' "$k" "$benchmark_log" >>"$status_file"
  fi
done

export DSV4_DSPARK_K=7
# shellcheck disable=SC1091
source "$SCRIPT_DIR/lib.sh"
docker_cmd run --rm --network none \
  --user "$(id -u):$(id -g)" \
  --volume "$R2_DIR:/r2:ro" \
  --volume "${RUNTIME_BASE:-/var/tmp/dsv4-a100-r2.1-20260830}:/runtime:rw" \
  --entrypoint python3 "$R2_IMAGE" \
  /r2/benchmarks/select_dspark_k.py \
  --k1 /runtime/dspark-k1/results/benchmark/dspark-k1-screen.csv \
  --k3 /runtime/dspark-k3/results/benchmark/dspark-k3-screen.csv \
  --k5 /runtime/dspark-k5/results/benchmark/dspark-k5-screen.csv \
  --k7 /runtime/dspark-k7/results/benchmark/dspark-k7-screen.csv \
  --output /runtime/dspark-k-sweep/decision.json
printf 'DSPARK_K_SWEEP=PASS\nstatus=%s\ndecision=%s\n' \
  "$status_file" "$sweep_root/decision.json"
