#!/usr/bin/env bash
set -euo pipefail

# Build-host only. Saves the already-compiled image and exact R2 source into a
# single Ubuntu 22.04 target delivery. The model weights are intentionally not
# duplicated.
# shellcheck disable=SC1091
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib.sh"
init_docker
for command in git gzip mktemp sha256sum tar; do require_command "$command"; done

default_source=$(cd -- "$ROOT_DIR/../deepseek-v4-flash-a100-r2-source-20260826" 2>/dev/null && pwd || true)
source_dir=${R2_SOURCE_DIR:-$default_source}
[[ -n "$source_dir" ]] || die 'set R2_SOURCE_DIR to the clean R2 source worktree'
[[ "$(git -C "$source_dir" rev-parse HEAD)" == "$R2_SOURCE_COMMIT" ]] || die \
  'R2 source commit mismatch during packaging'
git -C "$source_dir" diff --quiet
git -C "$source_dir" diff --cached --quiet
[[ -z "$(git -C "$source_dir" ls-files --others --exclude-standard)" ]] || die \
  'R2 source has untracked files'
git -C "$ROOT_DIR" diff --quiet
git -C "$ROOT_DIR" diff --cached --quiet
[[ -z "$(git -C "$ROOT_DIR" ls-files --others --exclude-standard)" ]] || die \
  'offline delivery repository has untracked files'
docker_cmd image inspect "$R2_IMAGE" >/dev/null 2>&1 || die \
  "precompiled image is missing: $R2_IMAGE"
revision=$(docker_cmd image inspect --format \
  '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$R2_IMAGE")
[[ "$revision" == "$R2_SOURCE_COMMIT" ]] || die 'precompiled image revision mismatch'
build_max_jobs=$(docker_cmd image inspect --format \
  '{{index .Config.Labels "com.deepseek.build.max-jobs"}}' "$R2_IMAGE")
[[ "$build_max_jobs" == 8 ]] || die 'precompiled image MAX_JOBS provenance mismatch'
build_nvcc_threads=$(docker_cmd image inspect --format \
  '{{index .Config.Labels "com.deepseek.build.nvcc-threads"}}' "$R2_IMAGE")
[[ "$build_nvcc_threads" == 1 ]] || die \
  'precompiled image NVCC_THREADS provenance mismatch'
cuda_arch=$(docker_cmd image inspect --format \
  '{{index .Config.Labels "com.deepseek.cuda.arch"}}' "$R2_IMAGE")
[[ "$cuda_arch" == 8.0 ]] || die 'precompiled image CUDA architecture mismatch'

project_name=${OFFLINE_PROJECT_NAME:-deepseek-v4-flash-a100-r2.1-offline-20260830}
output=${1:-$(dirname -- "$ROOT_DIR")/$project_name.tar.gz}
output_dir=$(cd -- "$(dirname -- "$output")" && pwd)
output="$output_dir/$(basename -- "$output")"
[[ ! -e "$output" ]] || die "output already exists: $output"

compressor=(gzip -n -1)
if command -v pigz >/dev/null 2>&1; then
  compressor=(pigz -n -1 -p "${OFFLINE_COMPRESSION_JOBS:-4}")
fi
staging=$(mktemp -d "${TMPDIR:-/tmp}/dsv4-r2-package.XXXXXX")
temporary=$(mktemp "$output_dir/.dsv4-r2.XXXXXX.tar.gz")
cleanup() { rm -rf -- "$staging"; rm -f -- "$temporary"; }
trap cleanup EXIT
head=$(git -C "$ROOT_DIR" rev-parse HEAD)
epoch=$(git -C "$ROOT_DIR" log -1 --format=%ct HEAD)
delivery_paths=(
  LICENSE README.md START-HERE.md CHANGELOG.md VERSION THIRD_PARTY.md
  start.sh start_one.sh start_two.sh stop.sh
  status.sh status_one.sh status_two.sh
  benchmark_one.sh benchmark_two.sh
  report.sh report_one.sh report_two.sh run-tests.sh
  benchmark_cache_profiles.sh benchmark_dspark_k.sh
  r2
)
git -C "$ROOT_DIR" archive --format=tar --prefix="$project_name/" "$head" \
  -- "${delivery_paths[@]}" | tar -xf - -C "$staging"

image_dir="$staging/$project_name/r2/images"
mkdir -p "$image_dir"
image_archive="$image_dir/dsv4-a100-r2-image.tar"
log 'saving the complete precompiled runtime image'
docker_cmd image save "$R2_IMAGE" >"$image_archive"
image_id=$(docker_cmd image inspect --format '{{.Id}}' "$R2_IMAGE")
image_sha=$(sha256sum "$image_archive" | awk '{print $1}')
(cd -- "$image_dir" && sha256sum "$(basename -- "$image_archive")" \
  >"$(basename -- "$image_archive").sha256")
{
  printf 'OFFLINE_R2_IMAGE_ID=%q\n' "$image_id"
  printf 'OFFLINE_R2_IMAGE_SHA256=%q\n' "$image_sha"
  printf 'OFFLINE_R2_SOURCE_COMMIT=%q\n' "$R2_SOURCE_COMMIT"
  printf 'OFFLINE_DELIVERY_GIT_SHA=%q\n' "$head"
  printf 'OFFLINE_TARGET_OS=%q\n' ubuntu-22.04
  printf 'OFFLINE_CUDA_ARCH=%q\n' "$cuda_arch"
  printf 'OFFLINE_BUILD_MAX_JOBS=%q\n' "$build_max_jobs"
  printf 'OFFLINE_BUILD_NVCC_THREADS=%q\n' "$build_nvcc_threads"
} >"$image_dir/offline-image.env"

source_archive="$staging/$project_name/r2/source/vllm-r2-source.tar.gz"
git -C "$source_dir" archive --format=tar.gz \
  --prefix="vllm-$R2_SOURCE_COMMIT/" "$R2_SOURCE_COMMIT" >"$source_archive"
(cd -- "$(dirname -- "$source_archive")" && sha256sum "$(basename -- "$source_archive")" \
  >"$(basename -- "$source_archive").sha256")

tar --sort=name --mtime="@$epoch" --owner=0 --group=0 --numeric-owner \
  -C "$staging" -cf - "$project_name" | "${compressor[@]}" >"$temporary"
mv -f -- "$temporary" "$output"
(cd -- "$output_dir" && sha256sum "$(basename -- "$output")" \
  >"$(basename -- "$output").sha256")
printf 'OFFLINE_DELIVERY=PASS\narchive=%s\nchecksum=%s.sha256\nimage_id=%s\nimage_sha256=%s\nsource_commit=%s\n' \
  "$output" "$output" "$image_id" "$image_sha" "$R2_SOURCE_COMMIT"
