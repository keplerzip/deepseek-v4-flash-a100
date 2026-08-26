#!/usr/bin/env bash
set -euo pipefail
# shellcheck disable=SC1091
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib.sh"
init_docker
container_exists "$CONTAINER_NAME" || die "container does not exist: $CONTAINER_NAME"
assert_owned_container "$CONTAINER_NAME"
exec "${DSV4_DOCKER_CMD[@]}" logs "$@" "$CONTAINER_NAME"
