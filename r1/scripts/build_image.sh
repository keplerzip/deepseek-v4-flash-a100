#!/usr/bin/env bash
set -euo pipefail

# shellcheck disable=SC1091
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

init_docker
docker_cmd image inspect "$BASE_IMAGE" >/dev/null 2>&1 || die \
  "required immutable base image is missing: $BASE_IMAGE"
if docker_cmd image inspect "$R1_IMAGE" >/dev/null 2>&1; then
  die "release tag already exists; refusing to overwrite it: $R1_IMAGE"
fi

base_id=$(docker_cmd image inspect --format '{{.Id}}' "$BASE_IMAGE")
base_created=$(docker_cmd image inspect --format '{{.Created}}' "$BASE_IMAGE")
printf 'base_image=%s\nbase_id=%s\nbase_created=%s\n' \
  "$BASE_IMAGE" "$base_id" "$base_created"

docker_cmd build \
  --network none \
  --pull=false \
  --build-arg "BASE_IMAGE=$BASE_IMAGE" \
  --build-arg "BASE_IMAGE_ID=$base_id" \
  --build-arg "RELEASE_VERSION=$R1_RELEASE" \
  --file "$ROOT_DIR/r1/docker/Dockerfile.r1" \
  --tag "$R1_IMAGE" \
  "$ROOT_DIR"

base_id_after=$(docker_cmd image inspect --format '{{.Id}}' "$BASE_IMAGE")
[[ "$base_id_after" == "$base_id" ]] || die \
  "base tag changed during build: before=$base_id after=$base_id_after"

revision=$(docker_cmd image inspect --format \
  '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$R1_IMAGE")
release=$(docker_cmd image inspect --format \
  '{{index .Config.Labels "com.deepseek.release"}}' "$R1_IMAGE")
recorded_base=$(docker_cmd image inspect --format \
  '{{index .Config.Labels "com.deepseek.base-image.id"}}' "$R1_IMAGE")
[[ "$revision" == "$BASE_VLLM_COMMIT" ]] || die "release revision label mismatch"
[[ "$release" == "$R1_RELEASE" ]] || die "release label mismatch"
[[ "$recorded_base" == "$base_id" ]] || die "recorded base image ID mismatch"

docker_cmd run --rm --network none --entrypoint python3 "$R1_IMAGE" \
  /opt/dsv4-r1/verify_installed_tree.py \
  --manifest /opt/dsv4-r1/manifests/r1-python.sha256

mkdir -p "$ROOT_DIR/r1/results"
docker_cmd image inspect "$BASE_IMAGE" "$R1_IMAGE" \
  >"$ROOT_DIR/r1/results/image-inspect.json"
docker_cmd image inspect --format '{{.Id}}' "$R1_IMAGE" \
  >"$ROOT_DIR/r1/results/release-image-id.txt"
printf 'IMAGE_BUILD=PASS\nrelease_image=%s\nrelease_id=%s\n' \
  "$R1_IMAGE" "$(<"$ROOT_DIR/r1/results/release-image-id.txt")"
