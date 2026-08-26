#!/usr/bin/env bash
set -euo pipefail

# shellcheck disable=SC1091
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib.sh"
ensure_runtime_dirs
init_docker
require_command awk
require_command sha256sum

image_dir="$R2_DIR/images"
archive="$image_dir/dsv4-a100-r2-image.tar"
checksum="$archive.sha256"
manifest="$image_dir/offline-image.env"
if [[ ! -f "$archive" || ! -f "$checksum" || ! -f "$manifest" ]]; then
  observed=$(docker_cmd image inspect --format '{{.Id}}' "$R2_IMAGE" 2>/dev/null || true)
  revision=$(docker_cmd image inspect --format \
    '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$R2_IMAGE" \
    2>/dev/null || true)
  observed_jobs=$(docker_cmd image inspect --format \
    '{{index .Config.Labels "com.deepseek.build.max-jobs"}}' "$R2_IMAGE" \
    2>/dev/null || true)
  observed_nvcc_threads=$(docker_cmd image inspect --format \
    '{{index .Config.Labels "com.deepseek.build.nvcc-threads"}}' "$R2_IMAGE" \
    2>/dev/null || true)
  observed_cuda_arch=$(docker_cmd image inspect --format \
    '{{index .Config.Labels "com.deepseek.cuda.arch"}}' "$R2_IMAGE" \
    2>/dev/null || true)
  if [[ -n "$observed" && "$revision" == "$R2_SOURCE_COMMIT" && \
        "$observed_jobs" == 8 && "$observed_nvcc_threads" == 1 && \
        "$observed_cuda_arch" == 8.0 ]]; then
    log 'offline tar is absent; reusing the exact locally prebuilt developer image'
    printf 'OFFLINE_IMAGE=PASS\nmode=preloaded-developer-image\nimage=%s\nid=%s\n' \
      "$R2_IMAGE" "$observed"
    exit 0
  fi
  die 'offline image payload is incomplete (source-only Git checkout cannot deploy)'
fi
# shellcheck disable=SC1090
source "$manifest"
: "${OFFLINE_R2_IMAGE_ID:?offline image ID is missing}"
: "${OFFLINE_R2_IMAGE_SHA256:?offline image checksum is missing}"
[[ "$(awk 'NF {print $1; exit}' "$checksum")" == "$OFFLINE_R2_IMAGE_SHA256" ]] || die \
  'image checksum file disagrees with manifest'
[[ "$(sha256sum "$archive" | awk '{print $1}')" == "$OFFLINE_R2_IMAGE_SHA256" ]] || die \
  'offline image archive checksum mismatch'

observed=$(docker_cmd image inspect --format '{{.Id}}' "$R2_IMAGE" 2>/dev/null || true)
if [[ -n "$observed" && "$observed" != "$OFFLINE_R2_IMAGE_ID" ]]; then
  die "image tag conflict: $R2_IMAGE expected=$OFFLINE_R2_IMAGE_ID observed=$observed"
fi
if [[ -z "$observed" ]]; then
  log 'loading precompiled runtime; target-side compilation and network are not used'
  docker_cmd image load --input "$archive"
fi
observed=$(docker_cmd image inspect --format '{{.Id}}' "$R2_IMAGE")
[[ "$observed" == "$OFFLINE_R2_IMAGE_ID" ]] || die 'loaded image ID mismatch'
revision=$(docker_cmd image inspect --format \
  '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$R2_IMAGE")
[[ "$revision" == "$R2_SOURCE_COMMIT" ]] || die 'loaded image source revision mismatch'
[[ "$(docker_cmd image inspect --format '{{index .Config.Labels "com.deepseek.build.max-jobs"}}' "$R2_IMAGE")" == 8 ]] || die \
  'loaded image MAX_JOBS provenance mismatch'
[[ "$(docker_cmd image inspect --format '{{index .Config.Labels "com.deepseek.build.nvcc-threads"}}' "$R2_IMAGE")" == 1 ]] || die \
  'loaded image NVCC_THREADS provenance mismatch'
[[ "$(docker_cmd image inspect --format '{{index .Config.Labels "com.deepseek.cuda.arch"}}' "$R2_IMAGE")" == 8.0 ]] || die \
  'loaded image CUDA architecture provenance mismatch'
printf 'OFFLINE_IMAGE=PASS\nimage=%s\nid=%s\nsha256=%s\n' \
  "$R2_IMAGE" "$observed" "$OFFLINE_R2_IMAGE_SHA256"
