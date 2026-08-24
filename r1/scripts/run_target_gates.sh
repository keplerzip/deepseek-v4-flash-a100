#!/usr/bin/env bash
set -euo pipefail

# End-to-end target gate. A completed performance CSV is resumable at
# RUNTIME_ROOT/results/performance-{one,two}/performance-matrix.csv.
# shellcheck disable=SC1091
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

init_docker
capture_failed_gate() {
  local gate_status=$?
  trap - EXIT
  if ((gate_status != 0)); then
    warn "target gate failed; collecting the available partial evidence"
    if [[ -f "$RESULT_DIR/performance-$SCHEME_ID/performance-matrix.csv" ]] \
      && docker_cmd image inspect "$R1_IMAGE" >/dev/null 2>&1; then
      "$R1_DIR/scripts/generate_result_artifact.sh" || \
        warn "partial performance report generation failed"
    fi
    "$R1_DIR/scripts/collect_results.sh" || \
      warn "partial evidence bundle generation failed"
  fi
  exit "$gate_status"
}
trap capture_failed_gate EXIT

"$R1_DIR/scripts/load_images.sh"
"$R1_DIR/scripts/stop_legacy_containers.sh"
"$R1_DIR/scripts/run_package_tests.sh"
"$R1_DIR/scripts/run_source_tests.sh"
if container_running "$CONTAINER_NAME"; then
  assert_owned_container "$CONTAINER_NAME"
else
  "$R1_DIR/scripts/start.sh"
fi
"$R1_DIR/scripts/run_acceptance.sh"
"$R1_DIR/scripts/run_tool_matrix.sh"
"$R1_DIR/scripts/run_stability.sh"
"$R1_DIR/scripts/run_performance.sh"
"$R1_DIR/scripts/generate_result_artifact.sh"
"$R1_DIR/scripts/collect_results.sh"
printf 'TARGET_GATES=PASS\n'
