#!/usr/bin/env bash
set -euo pipefail
# shellcheck disable=SC1091
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib.sh"
init_docker
printf 'R2_STATUS\n'
docker_cmd ps -a \
  --filter "label=com.deepseek.owner=$OWNER_LABEL" \
  --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}\t{{.Image}}'
if container_running "$CONTAINER_NAME"; then
  if http_ready; then
    printf 'api=ready\nurl=%s\n' "$(api_url /v1)"
    if command -v curl >/dev/null 2>&1; then
      auth=()
      [[ -z "${DSV4_API_KEY:-}" ]] || auth=(-H "Authorization: Bearer $DSV4_API_KEY")
      curl --noproxy '*' --silent --show-error "${auth[@]}" "$(api_url /v1/models)"
      printf '\n'
    fi
  else
    printf 'api=not_ready\n'
  fi
fi
