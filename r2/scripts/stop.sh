#!/usr/bin/env bash
set -euo pipefail
# shellcheck disable=SC1091
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib.sh"
init_docker
stopped=0
while IFS= read -r name; do
  [[ -n "$name" ]] || continue
  assert_owned_container "$name"
  if container_running "$name"; then
    log "stopping inference container: $name"
    docker_cmd stop --time 120 "$name" >/dev/null
    stopped=$((stopped + 1))
  fi
done < <(docker_cmd ps -a \
  --filter "label=com.deepseek.owner=$OWNER_LABEL" \
  --filter label=com.deepseek.role=inference \
  --format '{{.Names}}')
printf 'SERVICE_STOP=PASS\nstopped=%s\n' "$stopped"
