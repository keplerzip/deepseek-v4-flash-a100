#!/usr/bin/env bash
set -euo pipefail

# Load the precompiled, deduplicated offline image payload. Existing exact
# image IDs are reused; conflicting tags fail closed before docker load.
# shellcheck disable=SC1091
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

ensure_runtime_dirs
init_docker
require_command awk
require_command sha256sum

image_dir="$R1_DIR/images"
archive="$image_dir/dsv4-a100-r1-images.tar"
checksum_file="$archive.sha256"
manifest="$image_dir/offline-images.env"

[[ -f "$archive" ]] || die "offline image payload is missing: $archive"
[[ -f "$checksum_file" ]] || die "offline image checksum is missing: $checksum_file"
[[ -f "$manifest" ]] || die "offline image manifest is missing: $manifest"

# shellcheck disable=SC1090
source "$manifest"
: "${OFFLINE_BASE_IMAGE_ID:?offline manifest lacks OFFLINE_BASE_IMAGE_ID}"
: "${OFFLINE_R1_IMAGE_ID:?offline manifest lacks OFFLINE_R1_IMAGE_ID}"
: "${OFFLINE_SOURCE_TEST_IMAGE_ID:?offline manifest lacks OFFLINE_SOURCE_TEST_IMAGE_ID}"
: "${OFFLINE_IMAGE_ARCHIVE_SHA256:?offline manifest lacks archive digest}"

expected_from_file=$(awk 'NF {print $1; exit}' "$checksum_file")
[[ "$expected_from_file" == "$OFFLINE_IMAGE_ARCHIVE_SHA256" ]] || die \
  "offline image checksum and manifest disagree"
observed_archive=$(sha256sum "$archive" | awk '{print $1}')
[[ "$observed_archive" == "$OFFLINE_IMAGE_ARCHIVE_SHA256" ]] || die \
  "offline image payload checksum mismatch: expected=$OFFLINE_IMAGE_ARCHIVE_SHA256 observed=$observed_archive"

tags=("$BASE_IMAGE" "$R1_IMAGE" "$SOURCE_TEST_IMAGE")
ids=(
  "$OFFLINE_BASE_IMAGE_ID"
  "$OFFLINE_R1_IMAGE_ID"
  "$OFFLINE_SOURCE_TEST_IMAGE_ID"
)
all_present=1
for index in "${!tags[@]}"; do
  observed=$(docker_cmd image inspect --format '{{.Id}}' \
    "${tags[$index]}" 2>/dev/null || true)
  if [[ -z "$observed" ]]; then
    all_present=0
  elif [[ "$observed" != "${ids[$index]}" ]]; then
    die "image tag conflict: ${tags[$index]} expected=${ids[$index]} observed=$observed"
  fi
done

if ((all_present)); then
  log "all precompiled images already match the offline manifest"
else
  log "loading precompiled A100 images; no build or network access is used"
  docker_cmd image load --input "$archive"
fi

for index in "${!tags[@]}"; do
  observed=$(docker_cmd image inspect --format '{{.Id}}' "${tags[$index]}")
  [[ "$observed" == "${ids[$index]}" ]] || die \
    "loaded image ID mismatch: ${tags[$index]} expected=${ids[$index]} observed=$observed"
done

revision=$(docker_cmd image inspect --format \
  '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$R1_IMAGE")
[[ "$revision" == "$BASE_VLLM_COMMIT" ]] || die \
  "release image revision mismatch after load: $revision"
verify_image_tree "$BASE_IMAGE" base-python.sha256
verify_image_tree "$R1_IMAGE" r1-python.sha256
docker_cmd run --rm --network none --entrypoint python3 \
  "$SOURCE_TEST_IMAGE" -c \
  'import pytest, pytest_asyncio, tblib; assert pytest.__version__ == "9.1.1"; assert pytest_asyncio.__version__ == "1.4.0"; assert tblib.__version__ == "3.1.0"'

printf 'OFFLINE_IMAGES=PASS\narchive_sha256=%s\nbase_id=%s\nr1_id=%s\ntest_id=%s\n' \
  "$OFFLINE_IMAGE_ARCHIVE_SHA256" "$OFFLINE_BASE_IMAGE_ID" \
  "$OFFLINE_R1_IMAGE_ID" "$OFFLINE_SOURCE_TEST_IMAGE_ID"
