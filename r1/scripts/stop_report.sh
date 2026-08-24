#!/usr/bin/env bash
set -euo pipefail

# shellcheck disable=SC1091
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

init_docker
stopped=0
for report_container in "$REPORT_CONTAINER_NAME" dsv4-target-r1-report; do
  container_exists "$report_container" || continue
  assert_owned_container "$report_container"
  if container_running "$report_container"; then
    docker_cmd stop --time 10 "$report_container" >/dev/null
  fi
  stopped=$((stopped + 1))
done
printf 'REPORT_STOP=PASS\nscheme=%s\ncontainers_stopped=%s\n' \
  "$SCHEME_ID" "$stopped"
