#!/usr/bin/env bash
set -euo pipefail

# shellcheck disable=SC1091
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

ensure_runtime_dirs
http_ready || die "API is not ready: $(api_url /v1/models)"
timestamp=$(date -u +%Y%m%dT%H%M%SZ)
run_dir="$RESULT_DIR/api-contract-$timestamp"
mkdir -p "$run_dir"
output="$run_dir/summary.json"
host_user="$(id -u):$(id -g)"
capture_container_state "$CONTAINER_NAME" "$run_dir" container-before.json || true
snapshot_engine_processes "$CONTAINER_NAME" "$run_dir" process-before.json || true
set +e
docker_cmd run --rm --network host \
  --user "$host_user" \
  --volume "$R1_DIR:/audit:ro" \
  --volume "$run_dir:/results:rw" \
  --entrypoint python3 "$R1_IMAGE" \
  /audit/tests/api_contract_test.py \
  --origin "http://$HOST:$PORT" \
  --api-key "${DSV4_API_KEY:-}" \
  --model "$SERVED_MODEL_NAME" \
  --claude-model "$CLAUDE_MODEL_ALIAS" \
  --output /results/summary.json "$@" \
  2>&1 | tee "$run_dir/run.log"
harness_status=${PIPESTATUS[0]}
set -e
capture_container_state "$CONTAINER_NAME" "$run_dir" container-after.json || true
snapshot_engine_processes "$CONTAINER_NAME" "$run_dir" process-after.json || true
finalize_runtime_evidence "$run_dir" summary.json "$harness_status"
printf 'ACCEPTANCE_RESULT=%s\n' "$output"
