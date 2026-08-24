#!/usr/bin/env bash
set -euo pipefail

# shellcheck disable=SC1091
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

init_docker
test_image=$R1_IMAGE
if ! docker_cmd image inspect "$test_image" >/dev/null 2>&1; then
  test_image=$BASE_IMAGE
fi
docker_cmd image inspect "$test_image" >/dev/null 2>&1 || die \
  "neither the R1 nor base image is available for package tests"
docker_cmd run --rm --network none \
  --user "$(id -u):$(id -g)" \
  --volume "$ROOT_DIR:/src:ro" \
  --workdir /src \
  --entrypoint python3 "$test_image" \
  r1/tests/test_package_contract.py

while IFS= read -r script; do
  bash -n "$script"
done < <(find "$ROOT_DIR/r1" -type f -name '*.sh' -print | sort)

if command -v shellcheck >/dev/null 2>&1; then
  mapfile -t scripts < <(find "$ROOT_DIR/r1" -type f -name '*.sh' -print | sort)
  shellcheck "${scripts[@]}"
else
  printf 'WARNING: shellcheck is unavailable; bash syntax checks passed.\n' >&2
fi
printf 'PACKAGE_TESTS=PASS\n'
