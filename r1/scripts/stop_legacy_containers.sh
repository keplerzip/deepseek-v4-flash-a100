#!/usr/bin/env bash
set -euo pipefail

# Converge the two service containers created by the earliest f8ea5bb release.
# Exact names and all three ownership labels must match before a stop. Containers
# are retained for rollback/audit; this script never removes them or their image.
# shellcheck disable=SC1091
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

ensure_runtime_dirs
init_docker

legacy_commit=f8ea5bb163c161ef38b401d055cc5fd4a934091a
legacy_bundle=deepseek-v4-flash-a100-offline
legacy_names=(dsv4-target-only-f8ea5bb dsv4-dspark-f8ea5bb)
legacy_modes=(target-only dspark)
evidence="$RESULT_DIR/legacy-container-convergence.txt"
found=0
stopped=0

printf 'legacy_commit=%s\n' "$legacy_commit" >"$evidence"
for index in "${!legacy_names[@]}"; do
  name=${legacy_names[$index]}
  expected_mode=${legacy_modes[$index]}
  container_exists "$name" || continue
  found=$((found + 1))

  bundle=$(docker_cmd container inspect --format \
    '{{index .Config.Labels "com.deepseek.bundle"}}' "$name")
  mode=$(docker_cmd container inspect --format \
    '{{index .Config.Labels "com.deepseek.mode"}}' "$name")
  revision=$(docker_cmd container inspect --format \
    '{{index .Config.Labels "com.deepseek.vllm.commit"}}' "$name")
  [[ "$bundle" == "$legacy_bundle" && "$mode" == "$expected_mode" \
    && "$revision" == "$legacy_commit" ]] || die \
    "legacy container name is occupied by an unrecognized owner: $name"

  container_id=$(docker_cmd container inspect --format '{{.Id}}' "$name")
  image_id=$(docker_cmd container inspect --format '{{.Image}}' "$name")
  running_before=$(docker_cmd container inspect --format '{{.State.Running}}' "$name")
  if [[ "$running_before" == true ]]; then
    log "stopping earliest-release service container without removing it: $name"
    docker_cmd container stop --time 120 "$name" >/dev/null
    stopped=$((stopped + 1))
  fi
  running_after=$(docker_cmd container inspect --format '{{.State.Running}}' "$name")
  [[ "$running_after" == false ]] || die "legacy container did not stop: $name"
  printf 'name=%s id=%s image=%s mode=%s running_before=%s running_after=%s\n' \
    "$name" "$container_id" "$image_id" "$mode" "$running_before" \
    "$running_after" >>"$evidence"
done

printf 'LEGACY_CONVERGENCE=PASS\nfound=%s\nstopped=%s\nevidence=%s\n' \
  "$found" "$stopped" "$evidence"
