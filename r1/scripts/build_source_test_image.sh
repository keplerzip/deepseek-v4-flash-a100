#!/usr/bin/env bash
set -euo pipefail

# Build an offline pytest layer on top of the immutable R1 runtime image.
# shellcheck disable=SC1091
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

init_docker
require_command sha256sum
docker_cmd image inspect "$R1_IMAGE" >/dev/null 2>&1 || die \
  "required R1 image is missing: $R1_IMAGE"
r1_id=$(docker_cmd image inspect --format '{{.Id}}' "$R1_IMAGE")

if docker_cmd image inspect "$SOURCE_TEST_IMAGE" >/dev/null 2>&1; then
  parent=$(docker_cmd image inspect --format \
    '{{index .Config.Labels "com.deepseek.parent-image.id"}}' "$SOURCE_TEST_IMAGE")
  [[ "$parent" == "$r1_id" ]] || die \
    "existing source-test image belongs to a different R1 image: $parent"
  docker_cmd run --rm --network none --entrypoint python3 \
    "$SOURCE_TEST_IMAGE" -c \
    'import pytest, pytest_asyncio, tblib; assert pytest.__version__ == "9.1.1"; assert pytest_asyncio.__version__ == "1.4.0"; assert tblib.__version__ == "3.1.0"'
  printf 'SOURCE_TEST_IMAGE=PASS\nmode=reused\nimage=%s\n' "$SOURCE_TEST_IMAGE"
  exit 0
fi

(
  cd -- "$R1_DIR/test-wheelhouse"
  sha256sum -c sha256sums.txt
)
docker_cmd build \
  --network none \
  --pull=false \
  --build-arg "R1_IMAGE=$R1_IMAGE" \
  --build-arg "R1_IMAGE_ID=$r1_id" \
  --build-arg "RELEASE_VERSION=$R1_RELEASE" \
  --file "$R1_DIR/docker/Dockerfile.source-tests" \
  --tag "$SOURCE_TEST_IMAGE" \
  "$ROOT_DIR"

parent=$(docker_cmd image inspect --format \
  '{{index .Config.Labels "com.deepseek.parent-image.id"}}' "$SOURCE_TEST_IMAGE")
[[ "$parent" == "$r1_id" ]] || die "source-test parent image label mismatch"
docker_cmd image inspect --format '{{.Id}}' "$SOURCE_TEST_IMAGE" \
  >"$RESULT_DIR/source-test-image-digest.txt"
printf 'SOURCE_TEST_IMAGE=PASS\nmode=built\nimage=%s\ndigest=%s\n' \
  "$SOURCE_TEST_IMAGE" "$(<"$RESULT_DIR/source-test-image-digest.txt")"
