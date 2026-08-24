#!/usr/bin/env bash
set -euo pipefail

# Developer-side packager. It saves the three already-built images once, adds
# them to a clean Git archive, and creates the single target delivery tarball.
# shellcheck disable=SC1091
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

init_docker
for command in git gzip mktemp sha256sum tar; do
  require_command "$command"
done

compressor=(gzip -n -1)
compressor_description=gzip
if command -v pigz >/dev/null 2>&1; then
  compression_jobs=${OFFLINE_COMPRESSION_JOBS:-$(nproc)}
  [[ "$compression_jobs" =~ ^[1-9][0-9]*$ ]] || die \
    "OFFLINE_COMPRESSION_JOBS must be a positive integer"
  compressor=(pigz -n -1 -p "$compression_jobs")
  compressor_description="pigz/$compression_jobs"
fi

project_name=${OFFLINE_PROJECT_NAME:-deepseek-v4-flash-a100-target-r1-offline-20260820}
default_output="$(dirname -- "$ROOT_DIR")/$project_name.tar.gz"
output=${1:-$default_output}
output_dir=$(cd -- "$(dirname -- "$output")" && pwd)
output="$output_dir/$(basename -- "$output")"

[[ ! -e "$output" ]] || die "output already exists: $output"
git -C "$ROOT_DIR" diff --quiet
git -C "$ROOT_DIR" diff --cached --quiet
untracked=$(git -C "$ROOT_DIR" ls-files --others --exclude-standard)
[[ -z "$untracked" ]] || die \
  "untracked release files must be reviewed and committed before packaging"

images=("$BASE_IMAGE" "$R1_IMAGE" "$SOURCE_TEST_IMAGE")
for image in "${images[@]}"; do
  docker_cmd image inspect "$image" >/dev/null 2>&1 || die \
    "required precompiled image is missing: $image"
done
verify_image_tree "$BASE_IMAGE" base-python.sha256
verify_image_tree "$R1_IMAGE" r1-python.sha256
docker_cmd run --rm --network none --entrypoint python3 \
  "$SOURCE_TEST_IMAGE" -c \
  'import pytest, pytest_asyncio, tblib; assert pytest.__version__ == "9.1.1"; assert pytest_asyncio.__version__ == "1.4.0"; assert tblib.__version__ == "3.1.0"'

base_id=$(docker_cmd image inspect --format '{{.Id}}' "$BASE_IMAGE")
r1_id=$(docker_cmd image inspect --format '{{.Id}}' "$R1_IMAGE")
test_id=$(docker_cmd image inspect --format '{{.Id}}' "$SOURCE_TEST_IMAGE")
head=$(git -C "$ROOT_DIR" rev-parse HEAD)
source_epoch=$(git -C "$ROOT_DIR" log -1 --format=%ct HEAD)

staging=$(mktemp -d "${TMPDIR:-/tmp}/dsv4-offline-package.XXXXXX")
temporary_output=$(mktemp "$output_dir/.${project_name}.XXXXXX.tar.gz")
cleanup() {
  rm -rf -- "$staging"
  rm -f -- "$temporary_output"
}
trap cleanup EXIT

delivery_paths=(
  LICENSE
  START-HERE.md
  start.sh
  start_one.sh
  start_two.sh
  stop.sh
  status.sh
  status_one.sh
  status_two.sh
  run-tests.sh
  benchmark_one.sh
  benchmark_two.sh
  report.sh
  report_one.sh
  report_two.sh
  r1
  tests
  vllm/tokenizers/deepseek_v4.py
  vllm/tokenizers/deepseek_v32.py
  vllm/parser/deepseek_v4.py
  vllm/parser/deepseek_v32.py
  vllm/parser/engine/parser_engine_config.py
  vllm/parser/engine/streaming_parser_engine.py
  vllm/models/deepseek_v4/nvidia/model.py
  vllm/entrypoints/anthropic/serving.py
)
git -C "$ROOT_DIR" archive --format=tar --prefix="$project_name/" "$head" \
  -- "${delivery_paths[@]}" | tar -xf - -C "$staging"
cp "$staging/$project_name/START-HERE.md" \
  "$staging/$project_name/README.md"
image_dir="$staging/$project_name/r1/images"
mkdir -p "$image_dir"
image_archive="$image_dir/dsv4-a100-r1-images.tar"

log "saving deduplicated base, runtime, and test images"
# Open the destination as the invoking user.  With sudo -n docker, using
# Docker's --output would create a root-owned file that cannot be checksummed
# by an otherwise unprivileged release builder.
docker_cmd image save "${images[@]}" >"$image_archive"
image_sha=$(sha256sum "$image_archive" | awk '{print $1}')
(
  cd -- "$image_dir"
  sha256sum "$(basename -- "$image_archive")" \
    >"$(basename -- "$image_archive").sha256"
)
{
  printf 'OFFLINE_BASE_IMAGE_ID=%q\n' "$base_id"
  printf 'OFFLINE_R1_IMAGE_ID=%q\n' "$r1_id"
  printf 'OFFLINE_SOURCE_TEST_IMAGE_ID=%q\n' "$test_id"
  printf 'OFFLINE_IMAGE_ARCHIVE_SHA256=%q\n' "$image_sha"
  printf 'OFFLINE_DELIVERY_GIT_SHA=%q\n' "$head"
  printf 'OFFLINE_BUILD_MAX_JOBS=4\n'
  printf 'OFFLINE_BUILD_NVCC_THREADS=1\n'
  printf 'OFFLINE_CUDA_ARCH=8.0\n'
  printf 'OFFLINE_ARCHIVE_COMPRESSOR=%q\n' "$compressor_description"
} >"$image_dir/offline-images.env"

log "compressing final single-file delivery with $compressor_description"
tar --sort=name --mtime="@$source_epoch" --owner=0 --group=0 --numeric-owner \
  -C "$staging" -cf - "$project_name" \
  | "${compressor[@]}" >"$temporary_output"
mv -f -- "$temporary_output" "$output"
(
  cd -- "$output_dir"
  sha256sum "$(basename -- "$output")" >"$(basename -- "$output").sha256"
)
printf 'OFFLINE_DELIVERY=PASS\narchive=%s\nchecksum=%s.sha256\nimage_archive_sha256=%s\nbase_id=%s\nr1_id=%s\ntest_id=%s\n' \
  "$output" "$output" "$image_sha" "$base_id" "$r1_id" "$test_id"
