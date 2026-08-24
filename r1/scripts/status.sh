#!/usr/bin/env bash
set -euo pipefail

# shellcheck disable=SC1091
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

init_docker
printf 'scheme=%s\ngpus=%s\ntensor_parallel_size=%s\n' \
  "$SCHEME_ID" "$GPU_DEVICES" "$TENSOR_PARALLEL_SIZE"
owned_running=0
for container in "$CONTAINER_NAME" "${CONTAINER_NAME}-base-rollback"; do
  if container_exists "$container"; then
    assert_owned_container "$container"
    container_running "$container" && owned_running=1
    docker_cmd ps -a --filter "name=^/${container}$" \
      --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.ID}}'
  fi
done
if ((owned_running)) && http_ready; then
  printf 'api=ready host_url=%s\n' "$(api_url /v1)"
  printf 'docker_url=http://host.docker.internal:%s/v1\n' "$PORT"
  for container in "$CONTAINER_NAME" "${CONTAINER_NAME}-base-rollback"; do
    container_running "$container" || continue
    docker_cmd port "$container" "$PORT/tcp"
  done
  print_models
  printf '\n'
else
  printf 'api=not-ready host_url=%s\n' "$(api_url /v1)"
  exit 1
fi
