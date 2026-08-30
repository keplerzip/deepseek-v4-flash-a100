#!/usr/bin/env bash
set -euo pipefail

# Build-host only. The target package never calls this script.
# shellcheck disable=SC1091
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

init_docker
for command in git mktemp; do require_command "$command"; done

default_source=$(cd -- "$ROOT_DIR/../deepseek-v4-flash-a100-r2-source-20260826" 2>/dev/null && pwd || true)
source_dir=${R2_SOURCE_DIR:-$default_source}
if [[ -z "$source_dir" ]] || { [[ ! -d "$source_dir/.git" ]] && [[ ! -f "$source_dir/.git" ]]; }; then
  die 'set R2_SOURCE_DIR to the R2 vLLM Git worktree'
fi
build_max_jobs=${R2_BUILD_MAX_JOBS:-8}
build_nvcc_threads=${R2_BUILD_NVCC_THREADS:-1}
[[ "$build_max_jobs" == 8 ]] || die 'this release locks R2_BUILD_MAX_JOBS=8'
[[ "$build_nvcc_threads" == 1 ]] || die \
  'this release locks R2_BUILD_NVCC_THREADS=1'
observed_commit=$(git -C "$source_dir" rev-parse HEAD)
[[ "$observed_commit" == "$R2_SOURCE_COMMIT" ]] || die \
  "R2 source commit mismatch: expected=$R2_SOURCE_COMMIT observed=$observed_commit"
git -C "$source_dir" diff --quiet
git -C "$source_dir" diff --cached --quiet
[[ -z "$(git -C "$source_dir" ls-files --others --exclude-standard)" ]] || die \
  'R2 source worktree has untracked files'

if docker_cmd image inspect "$R2_IMAGE" >/dev/null 2>&1; then
  revision=$(docker_cmd image inspect --format \
    '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$R2_IMAGE")
  [[ "$revision" == "$R2_SOURCE_COMMIT" ]] || die \
    "existing image tag has another revision: $revision"
  observed_jobs=$(docker_cmd image inspect --format \
    '{{index .Config.Labels "com.deepseek.build.max-jobs"}}' "$R2_IMAGE")
  observed_nvcc_threads=$(docker_cmd image inspect --format \
    '{{index .Config.Labels "com.deepseek.build.nvcc-threads"}}' "$R2_IMAGE")
  if [[ "$observed_jobs" == "$build_max_jobs" && \
        "$observed_nvcc_threads" == "$build_nvcc_threads" ]]; then
    docker_cmd run --rm --network none \
      --volume "$R2_DIR:/audit:ro" \
      --entrypoint python3 "$R2_IMAGE" \
      /audit/scripts/verify_runtime_source.py
    printf 'R2_IMAGE=PASS\nmode=reused\nimage=%s\n' "$R2_IMAGE"
    exit 0
  fi
  log 'existing image lacks exact build-parallelism provenance; refreshing its final manifest'
fi

build_root=$(mktemp -d "${TMPDIR:-/tmp}/dsv4-r2-build.XXXXXX")
cleanup() { rm -rf -- "$build_root"; }
trap cleanup EXIT
context="$build_root/vllm"
git clone --quiet --no-local --no-checkout "$source_dir" "$context"
git -C "$context" checkout --quiet --detach "$R2_SOURCE_COMMIT"

log "building the complete SM80 runtime locally from $R2_SOURCE_COMMIT (MAX_JOBS=$build_max_jobs, NVCC_THREADS=$build_nvcc_threads)"
docker_cmd build \
  --progress plain \
  --pull=false \
  --target vllm-openai \
  --build-arg torch_cuda_arch_list=8.0 \
  --build-arg max_jobs="$build_max_jobs" \
  --build-arg nvcc_threads="$build_nvcc_threads" \
  --build-arg "VLLM_BUILD_COMMIT=$R2_SOURCE_COMMIT" \
  --build-arg VLLM_BUILD_PIPELINE=deepseek-v4-a100-r2-offline \
  --build-arg "VLLM_IMAGE_TAG=$R2_IMAGE" \
  --label "com.deepseek.release=$R2_RELEASE" \
  --label com.deepseek.cuda.arch=8.0 \
  --label "com.deepseek.build.max-jobs=$build_max_jobs" \
  --label "com.deepseek.build.nvcc-threads=$build_nvcc_threads" \
  --file "$context/docker/Dockerfile" \
  --tag "$R2_IMAGE" \
  "$context"

revision=$(docker_cmd image inspect --format \
  '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$R2_IMAGE")
[[ "$revision" == "$R2_SOURCE_COMMIT" ]] || die 'built image revision label mismatch'
[[ "$(docker_cmd image inspect --format '{{index .Config.Labels "com.deepseek.build.max-jobs"}}' "$R2_IMAGE")" == 8 ]] || die \
  'built image MAX_JOBS label mismatch'
[[ "$(docker_cmd image inspect --format '{{index .Config.Labels "com.deepseek.build.nvcc-threads"}}' "$R2_IMAGE")" == 1 ]] || die \
  'built image NVCC_THREADS label mismatch'
[[ "$(docker_cmd image inspect --format '{{index .Config.Labels "com.deepseek.cuda.arch"}}' "$R2_IMAGE")" == 8.0 ]] || die \
  'built image CUDA architecture label mismatch'
docker_cmd run --rm --network none --entrypoint python3 "$R2_IMAGE" -c \
  'import vllm; from vllm.entrypoints.serve.utils.model_limits import get_served_model_max_len; assert get_served_model_max_len("x", 16) == 16; print(vllm.__version__)'
docker_cmd run --rm --network none \
  --volume "$R2_DIR:/audit:ro" \
  --entrypoint python3 "$R2_IMAGE" \
  /audit/scripts/verify_runtime_source.py
ensure_runtime_dirs
docker_cmd image inspect "$R2_IMAGE" >"$RESULT_DIR/image-inspect.json"
printf 'R2_IMAGE=PASS\nmode=built\nimage=%s\nid=%s\n' "$R2_IMAGE" \
  "$(docker_cmd image inspect --format '{{.Id}}' "$R2_IMAGE")"
