#!/usr/bin/env bash
set -euo pipefail

# shellcheck disable=SC1091
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib.sh"
load_mode_config target-only
detect_runtime
[[ "$RUNTIME_KIND" == docker ]] || die "the reproducible image build requires Docker/BuildKit"

SOURCE_DIR="$ROOT_DIR/common/source/vllm-build"
SOURCE_ARCHIVE="$ROOT_DIR/common/source/vllm-f8ea5bb-source.tar.gz"
IMAGE_DIR="$ROOT_DIR/common/image"
MANIFEST_DIR="$ROOT_DIR/common/manifests"
LICENSE_DIR="$ROOT_DIR/common/licenses"
BUILD_LOG="$REPORT_DIR/image-build.log"
mkdir -p "$IMAGE_DIR" "$MANIFEST_DIR" "$LICENSE_DIR" "$REPORT_DIR"

[[ -d "$SOURCE_DIR/.git" ]] || die "fixed source checkout is missing: $SOURCE_DIR"
actual_commit=$(git -C "$SOURCE_DIR" rev-parse HEAD)
[[ "$actual_commit" == "$VLLM_COMMIT" ]] || \
  die "source checkout mismatch: $actual_commit (required $VLLM_COMMIT)"
[[ -z "$(git -C "$SOURCE_DIR" status --short)" ]] || \
  die "source checkout is dirty; refusing an unreproducible build"

git -C "$SOURCE_DIR" archive --format=tar.gz \
  --prefix="vllm-$VLLM_COMMIT/" HEAD >"$SOURCE_ARCHIVE"
cp "$SOURCE_DIR/LICENSE" "$LICENSE_DIR/vllm-Apache-2.0.txt"
printf '%s\n' "$VLLM_COMMIT" >"$MANIFEST_DIR/source-commit.txt"

build_args=(
  --file "$SOURCE_DIR/docker/Dockerfile" \
  --target vllm-openai \
  --tag "$IMAGE_NAME" \
  --label "org.opencontainers.image.source=$VLLM_REPOSITORY" \
  --label "org.opencontainers.image.revision=$VLLM_COMMIT" \
  --label "com.deepseek.bundle=$BUNDLE_LABEL" \
  --build-arg torch_cuda_arch_list=8.0 \
  --build-arg max_jobs="${MAX_JOBS:-2}" \
  --build-arg nvcc_threads="${NVCC_THREADS:-2}" \
  --build-arg VLLM_BUILD_COMMIT="$VLLM_COMMIT" \
  --build-arg VLLM_BUILD_PIPELINE=offline-bundle \
  --build-arg VLLM_IMAGE_TAG="$IMAGE_NAME" \
)
if [[ -n "${BUILD_MEMORY_LIMIT:-}" ]]; then
  build_args+=(--resource "memory=$BUILD_MEMORY_LIMIT")
fi
# Optional mainland/private PyPI mirror. Keep this opt-in so changing an index
# does not invalidate an already cached exact-commit image build.
if [[ -n "${BUILD_PIP_INDEX_URL:-}" ]]; then
  build_args+=(
    --build-arg "PIP_INDEX_URL=$BUILD_PIP_INDEX_URL"
    --build-arg "UV_INDEX_URL=$BUILD_PIP_INDEX_URL"
  )
fi

set +e
runtime build "${build_args[@]}" "$SOURCE_DIR" 2>&1 | tee "$BUILD_LOG"
build_rc=${PIPESTATUS[0]}
set -e
if ((build_rc != 0)); then
  {
    printf '\n## Image build blocker (%s)\n\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf 'The exact commit image build failed (exit %d). See `reports/image-build.log`.\n' "$build_rc"
    printf 'No alternate vLLM, commit, CUDA or Torch version was substituted.\n'
  } >>"$ROOT_DIR/docs/KNOWN-LIMITATIONS.md"
  exit "$build_rc"
fi

runtime image inspect "$IMAGE_NAME" >"$MANIFEST_DIR/image-inspect.json"
runtime run --rm --network none --entrypoint python3 "$IMAGE_NAME" \
  -m pip freeze --all >"$MANIFEST_DIR/pip-freeze.txt"
runtime run --rm --network none --entrypoint python3 "$IMAGE_NAME" -c '
import importlib.metadata as m
import json
items=[]
for d in m.distributions():
    name=d.metadata.get("Name")
    if name:
        items.append({"name":name,"version":d.version,"license":d.metadata.get("License")})
print(json.dumps(sorted(items,key=lambda x:x["name"].lower()),indent=2))
' >"$LICENSE_DIR/python-distribution-licenses.json"

runtime_versions=$(runtime run --rm --network none --entrypoint python3 "$IMAGE_NAME" -c '
import importlib.metadata as m, sys
import torch, vllm
print("python=" + sys.version.replace("\n", " "))
print("vllm=" + vllm.__version__)
print("torch=" + torch.__version__)
print("torch_cuda=" + str(torch.version.cuda))
for n in ("triton", "nvidia-nccl-cu13", "nvidia-nccl-cu12"):
    try: print(n + "=" + m.version(n))
    except m.PackageNotFoundError: pass
')
{
  printf 'built_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  printf 'build_host_os='; tr '\n' ' ' </etc/os-release; printf '\n'
  printf 'build_host_kernel=%s\n' "$(uname -a)"
  printf 'build_host_arch=%s\n' "$(uname -m)"
  printf 'docker='; runtime version --format '{{.Client.Version}}/{{.Server.Version}}'; printf '\n'
  printf 'repository=%s\ncommit=%s\nsource_status=clean\n' "$VLLM_REPOSITORY" "$VLLM_COMMIT"
  printf 'dockerfile_cuda=13.0.3\ndockerfile_ubuntu=22.04\ndockerfile_python=3.12\n'
  printf 'dockerfile_nccl=2.30.7\ntorch_cuda_arch_list=8.0\nmax_jobs=%s\nnvcc_threads=%s\n' \
    "${MAX_JOBS:-2}" "${NVCC_THREADS:-2}"
  printf '%s\n' "$runtime_versions"
} >"$MANIFEST_DIR/build-info.txt"

"$ROOT_DIR/scripts/verify_image.sh"

archive_tar="$IMAGE_DIR/${IMAGE_ARCHIVE_BASENAME}.tar"
archive_zst="$IMAGE_DIR/${IMAGE_ARCHIVE_BASENAME}.tar.zst"
rm -f "$archive_tar.tmp" "$archive_zst.tmp"
if [[ "${IMAGE_EXPORT_COMPRESSION:-none}" == zstd ]] && command -v zstd >/dev/null 2>&1; then
  log "exporting compressed image archive: $archive_zst"
  runtime save "$IMAGE_NAME" | zstd -T0 -10 -o "$archive_zst.tmp"
  mv -f "$archive_zst.tmp" "$archive_zst"
  rm -f "$archive_tar"
else
  log "exporting portable uncompressed image archive: $archive_tar"
  runtime save --output "$archive_tar.tmp" "$IMAGE_NAME"
  mv -f "$archive_tar.tmp" "$archive_tar"
  rm -f "$archive_zst"
fi

if [[ "${PREPARE_WHEELHOUSE:-1}" == 1 ]]; then
  set +e
  "$ROOT_DIR/scripts/prepare_wheelhouse.sh"
  wheelhouse_rc=$?
  set -e
  if ((wheelhouse_rc != 0)); then
    warn "native wheelhouse is incomplete (exit $wheelhouse_rc); primary image remains usable"
  fi
fi

"$ROOT_DIR/scripts/update_checksums.sh"
printf 'ONLINE_BUNDLE_PREPARATION=PASS\nimage=%s\nsource=%s\n' \
  "$(image_archive_path)" "$SOURCE_ARCHIVE"
