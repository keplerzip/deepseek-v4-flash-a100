#!/usr/bin/env bash
set -euo pipefail

# shellcheck disable=SC1091
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

ensure_runtime_dirs
require_command flock
init_docker
exec 9>"$CONTROL_DIR/start-stop.lock"
flock -n 9 || die "another start/stop operation is in progress"

stopped=0
for container in \
  "$SCHEME_ONE_CONTAINER_NAME" \
  "${SCHEME_ONE_CONTAINER_NAME}-base-rollback" \
  "$SCHEME_TWO_CONTAINER_NAME" \
  "${SCHEME_TWO_CONTAINER_NAME}-base-rollback"; do
  container_exists "$container" || continue
  assert_owned_container "$container"
  container_log_dir=$RUNTIME_BASE/one/logs
  if [[ "$container" == "$SCHEME_TWO_CONTAINER_NAME"* ]]; then
    container_log_dir=$RUNTIME_BASE/two/logs
  fi
  mkdir -p "$container_log_dir"
  docker_cmd logs --timestamps "$container" \
    >"$container_log_dir/${container}-stop-$(date -u +%Y%m%dT%H%M%SZ).log" \
    2>&1 || true
  if container_running "$container"; then
    docker_cmd stop --time 120 "$container" >/dev/null
  fi
  stopped=$((stopped + 1))
done
"$R1_DIR/scripts/stop_legacy_containers.sh"
printf 'SERVICE_STOP=PASS\nowned_containers_stopped=%s\n' "$stopped"
