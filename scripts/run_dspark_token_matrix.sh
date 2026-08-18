#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
# shellcheck disable=SC1091
source "$ROOT_DIR/scripts/lib.sh"
load_mode_config dspark
detect_runtime
if [[ -s "$RUN_DIR/dsv4-a100.lock" ]] || container_exists "$CONTAINER_NAME"; then
  die "stop the current bundle service before running the DSpark 3/5/7 matrix"
fi

report="$REPORT_DIR/dspark-speculative-token-matrix-$(date -u +%Y%m%dT%H%M%SZ).txt"
for tokens in 3 5 7; do
  {
    printf '\nnum_speculative_tokens=%s\n' "$tokens"
    printf 'started_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  } >>"$report"
  set +e
  DSPARK_NUM_SPECULATIVE_TOKENS=$tokens "$ROOT_DIR/dspark/start.sh" >>"$report" 2>&1
  start_rc=$?
  set -e
  printf 'start_exit=%s\n' "$start_rc" >>"$report"
  if ((start_rc == 0)); then
    DSPARK_NUM_SPECULATIVE_TOKENS=$tokens \
      PROMPT_LENGTHS=1024 OUTPUT_LENGTHS=128 CONCURRENCY_LEVELS=1 \
      "$ROOT_DIR/dspark/benchmark.sh" >>"$report" 2>&1 || true
  fi
  DSPARK_NUM_SPECULATIVE_TOKENS=$tokens "$ROOT_DIR/dspark/stop.sh" >>"$report" 2>&1 || true
done
printf 'Matrix report: %s\n' "$report"
printf 'The 0731 checkpoint may reject 3 because its DSpark block size is 5; that result is retained.\n'
