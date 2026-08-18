#!/usr/bin/env bash
set -euo pipefail

# shellcheck disable=SC1091
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib.sh"
load_mode_config target-only
detect_runtime
[[ "$RUNTIME_KIND" == docker ]] || die "wheelhouse preparation currently requires Docker build stages"

SOURCE_DIR="$ROOT_DIR/common/source/vllm-build"
WHEELHOUSE="$ROOT_DIR/common/wheelhouse"
BUILD_IMAGE=dsv4-a100-vllm-wheel-build:f8ea5bb
BUILD_CONTAINER=dsv4-wheel-extract-f8ea5bb
LOG_FILE="$REPORT_DIR/wheelhouse-build.log"
mkdir -p "$WHEELHOUSE" "$REPORT_DIR"
rm -f "$WHEELHOUSE/WHEELHOUSE_COMPLETE"

wheel_build_args=(
  --file "$SOURCE_DIR/docker/Dockerfile" \
  --target build \
  --tag "$BUILD_IMAGE" \
  --build-arg torch_cuda_arch_list=8.0 \
  --build-arg max_jobs="${MAX_JOBS:-2}" \
  --build-arg nvcc_threads="${NVCC_THREADS:-2}" \
  --build-arg VLLM_BUILD_COMMIT="$VLLM_COMMIT" \
)
if [[ -n "${BUILD_MEMORY_LIMIT:-}" ]]; then
  wheel_build_args+=(--resource "memory=$BUILD_MEMORY_LIMIT")
fi
runtime build "${wheel_build_args[@]}" "$SOURCE_DIR" 2>&1 | tee "$LOG_FILE"

if runtime container inspect "$BUILD_CONTAINER" >/dev/null 2>&1; then
  owner=$(runtime inspect --format '{{index .Config.Labels "com.deepseek.bundle"}}' "$BUILD_CONTAINER" 2>/dev/null || true)
  [[ "$owner" == "$BUNDLE_LABEL" ]] || die "temporary container name collision: $BUILD_CONTAINER"
  runtime rm "$BUILD_CONTAINER" >/dev/null
fi
runtime create --name "$BUILD_CONTAINER" \
  --label "com.deepseek.bundle=$BUNDLE_LABEL" "$BUILD_IMAGE" >/dev/null
runtime cp "$BUILD_CONTAINER:/workspace/dist/." "$WHEELHOUSE/"
runtime cp "$BUILD_CONTAINER:/tmp/ep_kernels_workspace/dist/." "$WHEELHOUSE/" 2>/dev/null || true
runtime rm "$BUILD_CONTAINER" >/dev/null

runtime run --rm --network none --entrypoint python3 "$IMAGE_NAME" \
  -m pip freeze --all >"$WHEELHOUSE/pip-freeze.txt"
runtime run --rm --network none --entrypoint python3 "$IMAGE_NAME" -c '
import importlib.metadata as m
for d in sorted(m.distributions(), key=lambda x: (x.metadata.get("Name") or "").lower()):
    name = d.metadata.get("Name")
    if name:
        print(f"{name}=={d.version}")
' >"$WHEELHOUSE/requirements-lock.txt"

download_log="$REPORT_DIR/wheelhouse-download.log"
PYPI_MIRROR_URL="${PYPI_MIRROR_URL:-https://pypi.tuna.tsinghua.edu.cn/simple}"
set +e
runtime run --rm \
  --network host \
  --volume "$WHEELHOUSE:/wheelhouse:rw" \
  --entrypoint python3 \
  "$IMAGE_NAME" \
  -m pip download \
  --dest /wheelhouse \
  --only-binary=:all: \
  --find-links /wheelhouse \
  --index-url "$PYPI_MIRROR_URL" \
  --extra-index-url https://download.pytorch.org/whl/cu130 \
  --extra-index-url https://flashinfer.ai/whl/cu130 \
  --requirement /wheelhouse/requirements-lock.txt \
  >"$download_log" 2>&1
download_rc=$?
set -e

if ((download_rc != 0)); then
  {
    printf '# Native wheelhouse is incomplete\n\n'
    printf 'The exact container image is complete and remains the primary offline runtime.\n'
    printf 'At least one installed dependency could not be recovered as a binary wheel.\n'
    printf 'See `reports/wheelhouse-download.log`; no fallback-complete marker was written.\n'
  } >"$WHEELHOUSE/WHEELHOUSE_INCOMPLETE.md"
  {
    printf '\n## Wheelhouse build blocker (%s)\n\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf 'Binary-only dependency download failed. See `reports/wheelhouse-download.log`. '
    printf 'The container artifact is unaffected; native fallback is not accepted as complete.\n'
  } >>"$ROOT_DIR/docs/KNOWN-LIMITATIONS.md"
  exit 4
fi

runtime run --rm --network none \
  --volume "$WHEELHOUSE:/wheelhouse:ro" \
  --entrypoint python3 \
  "$IMAGE_NAME" \
  -m pip install \
  --dry-run \
  --ignore-installed \
  --no-index \
  --find-links /wheelhouse \
  --requirement /wheelhouse/requirements-lock.txt \
  >"$REPORT_DIR/wheelhouse-offline-resolve.log" 2>&1

printf 'commit=%s\npython=3.12\nplatform=linux_x86_64\n' "$VLLM_COMMIT" \
  >"$WHEELHOUSE/WHEELHOUSE_COMPLETE"
rm -f "$WHEELHOUSE/WHEELHOUSE_INCOMPLETE.md"
printf 'WHEELHOUSE=COMPLETE\n'
