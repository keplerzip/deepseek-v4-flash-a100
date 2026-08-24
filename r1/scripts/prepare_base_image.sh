#!/usr/bin/env bash
set -euo pipefail

# Developer-side build or verification of the immutable fixed-base image.
# Target deployment uses load_images.sh and never invokes this compiler path.
# All privileged work goes through docker_cmd.
# shellcheck disable=SC1091
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

ensure_runtime_dirs
init_docker
require_command awk
require_command git
require_command mktemp
require_command sha256sum

source_archive="$R1_DIR/base/vllm-1281004-source.tar.gz"
checksum_file="$R1_DIR/base/vllm-1281004-source.tar.gz.sha256"
[[ -f "$source_archive" ]] || die "fixed-base source archive is missing: $source_archive"
[[ -f "$checksum_file" ]] || die "fixed-base checksum is missing: $checksum_file"

if docker_cmd image inspect "$BASE_IMAGE" >/dev/null 2>&1; then
  revision=$(docker_cmd image inspect --format \
    '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$BASE_IMAGE")
  [[ "$revision" == "$BASE_VLLM_COMMIT" ]] || die \
    "existing base tag has the wrong revision: $revision"
  verify_image_tree "$BASE_IMAGE" base-python.sha256
  printf 'BASE_IMAGE=PASS\nmode=reused\nimage=%s\n' "$BASE_IMAGE"
  exit 0
fi

expected=$(awk 'NF {print $1; exit}' "$checksum_file")
observed=$(sha256sum "$source_archive" | awk '{print $1}')
[[ "$observed" == "$expected" ]] || die \
  "fixed-base source archive checksum mismatch: expected=$expected observed=$observed"

git -C "$ROOT_DIR" cat-file -e "$BASE_VLLM_COMMIT^{commit}" 2>/dev/null || die \
  "the exact base commit is unavailable in the developer Git repository"
base_context=$(mktemp -d "${TMPDIR:-/tmp}/dsv4-base-1281004.XXXXXX")
cleanup() {
  rm -rf -- "$base_context"
}
trap cleanup EXIT
context="$base_context/vllm-1281004"
git clone --quiet --no-local --no-checkout "$ROOT_DIR" "$context"
git -C "$context" checkout --quiet --detach "$BASE_VLLM_COMMIT"
[[ "$(git -C "$context" rev-parse HEAD)" == "$BASE_VLLM_COMMIT" ]] || die \
  "developer build context did not resolve the exact base commit"
[[ -f "$context/docker/Dockerfile" ]] || die \
  "fixed-base Git context has an unexpected layout"

log "developer build of immutable base from $BASE_VLLM_COMMIT with four CPU cores"
docker_cmd build \
  --progress plain \
  --resource "cpu-quota=${BASE_BUILD_CPU_QUOTA:-400000}" \
  --pull=false \
  --target vllm-openai \
  --build-arg "torch_cuda_arch_list=8.0" \
  --build-arg "max_jobs=${BASE_BUILD_MAX_JOBS:-4}" \
  --build-arg "nvcc_threads=${BASE_BUILD_NVCC_THREADS:-1}" \
  --build-arg "VLLM_BUILD_COMMIT=$BASE_VLLM_COMMIT" \
  --build-arg "VLLM_BUILD_PIPELINE=deepseek-v4-target-r1-offline" \
  --build-arg "VLLM_IMAGE_TAG=$BASE_IMAGE" \
  --label "org.opencontainers.image.source=https://github.com/haosdent/vllm.git" \
  --label "org.opencontainers.image.revision=$BASE_VLLM_COMMIT" \
  --label "org.opencontainers.image.version=$BASE_IMAGE" \
  --label "com.deepseek.base-lock=$BASE_VLLM_COMMIT" \
  --file "$context/docker/Dockerfile" \
  --tag "$BASE_IMAGE" \
  "$context"

revision=$(docker_cmd image inspect --format \
  '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$BASE_IMAGE")
[[ "$revision" == "$BASE_VLLM_COMMIT" ]] || die \
  "built base image revision label mismatch: $revision"
verify_image_tree "$BASE_IMAGE" base-python.sha256
docker_cmd image inspect "$BASE_IMAGE" >"$RESULT_DIR/base-image-inspect.json"
printf 'BASE_IMAGE=PASS\nmode=built\nimage=%s\nimage_id=%s\n' \
  "$BASE_IMAGE" "$(docker_cmd image inspect --format '{{.Id}}' "$BASE_IMAGE")"
