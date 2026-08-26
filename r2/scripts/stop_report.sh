#!/usr/bin/env bash
set -euo pipefail
# shellcheck disable=SC1091
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib.sh"
if container_exists "$REPORT_CONTAINER_NAME"; then
  assert_owned_container "$REPORT_CONTAINER_NAME"
  container_running "$REPORT_CONTAINER_NAME" && docker_cmd stop --time 15 "$REPORT_CONTAINER_NAME" >/dev/null
fi
printf 'REPORT_STOP=PASS\ncontainer=%s\n' "$REPORT_CONTAINER_NAME"
