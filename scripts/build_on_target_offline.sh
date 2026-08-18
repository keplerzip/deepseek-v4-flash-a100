#!/usr/bin/env bash
set -euo pipefail

# shellcheck disable=SC1091
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib.sh"
load_mode_config target-only
detect_runtime
[[ "$RUNTIME_KIND" == docker ]] || die "target compilation currently requires Docker"

SEED_IMAGE=dsv4-a100-build-seed:f8ea5bb
SEED_ARCHIVE="$ROOT_DIR/common/image/dsv4-a100-build-seed-f8ea5bb.tar"
BUILD_CONTAINER=dsv4-a100-offline-compiler-f8ea5bb
OUTPUT_DIR="$ROOT_DIR/common/target-build-output"
BUILD_LOG="$REPORT_DIR/target-offline-build.log"
mkdir -p "$OUTPUT_DIR" "$REPORT_DIR" "$ROOT_DIR/common/manifests"

[[ "$(uname -m)" == x86_64 ]] || die "target must be x86_64"
if [[ -r /etc/os-release ]]; then
  # shellcheck disable=SC1091
  source /etc/os-release
  [[ "${ID:-}" == ubuntu && "${VERSION_ID:-}" == 22.04 ]] || \
    die "validated target OS is Ubuntu 22.04; found ${ID:-unknown} ${VERSION_ID:-unknown}"
fi
[[ -s "$SEED_ARCHIVE" ]] || die "seed archive is missing: $SEED_ARCHIVE"

free_kib=$(df -Pk "$ROOT_DIR" | awk 'NR == 2 {print $4}')
required_kib=$((100 * 1024 * 1024))
((free_kib >= required_kib)) || die "at least 100 GiB free disk is required for offline compilation"
docker_root=$(runtime info --format '{{.DockerRootDir}}' 2>/dev/null || true)
if [[ -n "$docker_root" ]]; then
  docker_free_kib=$(df -Pk "$docker_root" 2>/dev/null | awk 'NR == 2 {print $4}' || true)
  if [[ "$docker_free_kib" =~ ^[0-9]+$ ]]; then
    docker_required_kib=$((80 * 1024 * 1024))
    ((docker_free_kib >= docker_required_kib)) || \
      die "Docker root $docker_root needs at least 80 GiB free for compiler layers"
  else
    warn "could not determine free space for Docker root: $docker_root"
  fi
fi

if ! runtime image inspect "$SEED_IMAGE" >/dev/null 2>&1; then
  log "loading build seed image"
  runtime load --input "$SEED_ARCHIVE"
fi
revision=$(runtime image inspect --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$SEED_IMAGE")
kind=$(runtime image inspect --format '{{index .Config.Labels "com.deepseek.image-kind"}}' "$SEED_IMAGE")
[[ "$revision" == "$VLLM_COMMIT" && "$kind" == target-build-seed ]] || \
  die "seed image identity/commit mismatch"

if image_present; then
  existing_revision=$(runtime image inspect --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$IMAGE_NAME")
  [[ "$existing_revision" == "$VLLM_COMMIT" ]] || \
    die "runtime image tag already exists with a different revision: $IMAGE_NAME"
  log "runtime image already exists at the exact commit; verifying it"
  "$ROOT_DIR/scripts/verify_image.sh"
  exit 0
fi

if container_exists "$BUILD_CONTAINER"; then
  owner=$(runtime inspect --format '{{index .Config.Labels "com.deepseek.bundle"}}' "$BUILD_CONTAINER" 2>/dev/null || true)
  [[ "$owner" == "$BUNDLE_LABEL" ]] || die "refusing to touch unrelated container $BUILD_CONTAINER"
  state=$(runtime inspect --format '{{.State.Running}}' "$BUILD_CONTAINER")
  if [[ "$state" == true ]]; then
    die "an earlier owned compiler container is still running: $BUILD_CONTAINER"
  fi
  die "an earlier failed compiler container was preserved for debugging: $BUILD_CONTAINER; inspect it, then run: $(printf '%q ' "${RUNTIME_CMD[@]}")rm $BUILD_CONTAINER"
fi

log "starting network-isolated compiler container"
runtime run -d --network none --name "$BUILD_CONTAINER" \
  --label "com.deepseek.bundle=$BUNDLE_LABEL" \
  --label 'com.deepseek.mode=offline-compiler' \
  --shm-size 16g \
  -v "$OUTPUT_DIR:/offline-output" \
  -v "$ROOT_DIR/common/offline-build/files/build_inside_seed.sh:/usr/local/bin/build_inside_seed.sh:ro" \
  --entrypoint bash "$SEED_IMAGE" -lc 'exec sleep infinity' >/dev/null

set +e
runtime exec \
  -e "TARGET_BUILD_JOBS=${TARGET_BUILD_JOBS:-16}" \
  -e "NVCC_THREADS=${NVCC_THREADS:-1}" \
  "$BUILD_CONTAINER" /usr/local/bin/build_inside_seed.sh 2>&1 | tee "$BUILD_LOG"
build_rc=${PIPESTATUS[0]}
set -e
if ((build_rc != 0)); then
  runtime stop --time 10 "$BUILD_CONTAINER" >/dev/null || true
  {
    printf '\n## Target offline compile blocker (%s)\n\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf 'The network-isolated target compile failed (exit %d). See `reports/target-offline-build.log`.\n' "$build_rc"
    printf 'The owned compiler container `%s` was preserved for inspection. No alternate commit or dependency was substituted.\n' "$BUILD_CONTAINER"
  } >>"$ROOT_DIR/docs/KNOWN-LIMITATIONS.md"
  exit "$build_rc"
fi

log "committing the compiled runtime image"
runtime commit \
  --change 'ENTRYPOINT ["vllm","serve"]' \
  --change 'CMD []' \
  --change 'WORKDIR /workspace' \
  --change 'ENV HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HF_DATASETS_OFFLINE=1 VLLM_NO_USAGE_STATS=1 DO_NOT_TRACK=1 TORCH_CUDA_ARCH_LIST=8.0' \
  --change "LABEL org.opencontainers.image.source=$VLLM_REPOSITORY" \
  --change "LABEL org.opencontainers.image.revision=$VLLM_COMMIT" \
  --change "LABEL com.deepseek.bundle=$BUNDLE_LABEL" \
  --change 'LABEL com.deepseek.image-kind=runtime' \
  "$BUILD_CONTAINER" "$IMAGE_NAME" >/dev/null

runtime image inspect "$IMAGE_NAME" >"$ROOT_DIR/common/manifests/image-inspect.json"
cp "$OUTPUT_DIR/manifests/pip-freeze.txt" "$ROOT_DIR/common/manifests/pip-freeze.txt"
cp "$OUTPUT_DIR/manifests/target-build-info.txt" "$ROOT_DIR/common/manifests/build-info.txt"
"$ROOT_DIR/scripts/verify_image.sh"
runtime rm -f "$BUILD_CONTAINER" >/dev/null
printf 'TARGET_OFFLINE_IMAGE_BUILD=PASS\nimage=%s\nnext=%s\n' \
  "$IMAGE_NAME" "$ROOT_DIR/scripts/target_acceptance.sh preflight"
