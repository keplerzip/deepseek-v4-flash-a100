#!/usr/bin/env bash
set -euo pipefail

# shellcheck disable=SC1091
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib.sh"
load_mode_config target-only
detect_runtime
[[ "$RUNTIME_KIND" == docker ]] || die "seed image preparation requires Docker/BuildKit"
[[ "$(uname -m)" == x86_64 ]] || die "seed image must be prepared on x86_64"

SOURCE_DIR="$ROOT_DIR/common/source/vllm-build"
OFFLINE_DIR="$ROOT_DIR/common/offline-build"
VENDOR_DIR="$OFFLINE_DIR/vendor-src"
VENDOR_EXPORT_DIR="$OFFLINE_DIR/vendor-export"
ARTIFACT_DIR="$OFFLINE_DIR/artifacts"
SEED_FILES_DIR="$OFFLINE_DIR/files"
SEED_IMAGE=dsv4-a100-build-seed:f8ea5bb
BASE_IMAGE=dsv4-a100-build-base:f8ea5bb
RUST_IMAGE=dsv4-a100-rust-artifacts:f8ea5bb
SEED_ARCHIVE="$ROOT_DIR/common/image/dsv4-a100-build-seed-f8ea5bb.tar"
MANIFEST="$ROOT_DIR/common/manifests/target-build-seed.txt"
VENDOR_LICENSE_DIR="$ROOT_DIR/common/licenses/vendor"
mkdir -p "$VENDOR_DIR" "$ARTIFACT_DIR" "$ROOT_DIR/common/image" \
  "$ROOT_DIR/common/manifests" "$VENDOR_LICENSE_DIR" "$REPORT_DIR"

[[ -d "$SOURCE_DIR/.git" ]] || die "fixed source checkout is missing: $SOURCE_DIR"
[[ "$(git -C "$SOURCE_DIR" rev-parse HEAD)" == "$VLLM_COMMIT" ]] || die "source commit mismatch"
[[ -z "$(git -C "$SOURCE_DIR" status --short)" ]] || die "fixed source checkout is dirty"

clone_exact() {
  local name=$1 repo=$2 ref=$3 dir="$VENDOR_DIR/$name"
  local marker="$dir/.dsv4-required-ref"
  mkdir -p "$dir"
  if [[ ! -d "$dir/.git" ]]; then
    git -C "$dir" init -q
    git -C "$dir" remote add origin "$repo"
  else
    git -C "$dir" remote set-url origin "$repo"
  fi
  if [[ ! -f "$marker" || "$(<"$marker")" != "$ref" ]] || \
     ! git -C "$dir" cat-file -e HEAD^{commit} 2>/dev/null; then
    git -C "$dir" fetch --depth=1 --no-tags origin "$ref"
    git -C "$dir" checkout -q --detach FETCH_HEAD
    printf '%s\n' "$ref" >"$marker"
  fi
  # A tag resolves to a commit, so compare the peeled object as well.
  git -C "$dir" submodule sync --recursive
  git -C "$dir" -c protocol.file.allow=always submodule update \
    --init --recursive --depth=1 --jobs "${GIT_SUBMODULE_JOBS:-8}"
}

declare -a vendors=(
  'cutlass|https://github.com/NVIDIA/cutlass.git|v4.4.2'
  'triton|https://github.com/triton-lang/triton.git|v3.5.1'
  'deepgemm|https://github.com/vllm-project/DeepGEMM.git|f5a76426fa084087169693fd0cd815223576d6e9'
  'msa|https://github.com/vllm-project/MSA.git|890aaa1a37a598ad17ccff0827fea21540d381fa'
  'flashmla|https://github.com/vllm-project/FlashMLA.git|a8f794d1251cbfd88a5011445dd5582289c727e4'
  'flashkda|https://github.com/vllm-project/FlashKDA.git|a3e42bbbece3bb38f7c426b880315294a336e82f'
  'qutlass|https://github.com/IST-DASLab/qutlass.git|e74319e3405ce6d71965732880f5dc1f52371f64'
  'tml-fa4|https://github.com/vllm-project/tml-fa4.git|b206834606ed5b5f21f8eed6b0683f528ea9cf7d'
  'vllm-flash-attention|https://github.com/vllm-project/flash-attention.git|28e862d21806bc3580207aa0ad4e2759151e9827'
)

log "collecting pinned CMake/FetchContent repositories"
download_jobs=${VENDOR_DOWNLOAD_JOBS:-4}
running=0
declare -a pids=() names=()
for item in "${vendors[@]}"; do
  IFS='|' read -r name repo ref <<<"$item"
  (clone_exact "$name" "$repo" "$ref") >"$REPORT_DIR/vendor-$name.log" 2>&1 &
  pids+=("$!"); names+=("$name"); running=$((running + 1))
  if ((running >= download_jobs)); then
    idx=$((${#pids[@]} - running))
    if ! wait "${pids[$idx]}"; then
      sed -n '1,240p' "$REPORT_DIR/vendor-${names[$idx]}.log" >&2
      die "vendor download failed: ${names[$idx]}"
    fi
    running=$((running - 1))
  fi
done
first_unwaited=$((${#pids[@]} - running))
for ((i=first_unwaited; i<${#pids[@]}; i++)); do
  if ! wait "${pids[$i]}"; then
    sed -n '1,240p' "$REPORT_DIR/vendor-${names[$i]}.log" >&2
    die "vendor download failed: ${names[$i]}"
  fi
done

{
  printf 'seed_format=target-offline-build-v1\n'
  printf 'vllm_repository=%s\nvllm_commit=%s\n' "$VLLM_REPOSITORY" "$VLLM_COMMIT"
  printf 'seed_image=%s\nbase_image=%s\n' "$SEED_IMAGE" "$BASE_IMAGE"
  for item in "${vendors[@]}"; do
    IFS='|' read -r name repo ref <<<"$item"
    printf 'vendor.%s.repo=%s\nvendor.%s.ref=%s\nvendor.%s.head=%s\n' \
      "$name" "$repo" "$name" "$ref" "$name" "$(git -C "$VENDOR_DIR/$name" rev-parse HEAD)"
    git -C "$VENDOR_DIR/$name" submodule status --recursive | \
      sed "s|^|vendor.$name.submodule=|"
  done
} >"$MANIFEST"
cp "$MANIFEST" "$VENDOR_DIR/VENDOR-MANIFEST.txt"
for item in "${vendors[@]}"; do
  IFS='|' read -r name _ _ <<<"$item"
  license_file=$(find "$VENDOR_DIR/$name" -maxdepth 1 -type f \
    \( -iname 'LICENSE*' -o -iname 'COPYING*' \) -print -quit)
  if [[ -n "$license_file" ]]; then
    cp "$license_file" "$VENDOR_LICENSE_DIR/${name}-$(basename "$license_file")"
  fi
done

# Git object databases make the transferable image needlessly large. Keep the
# resumable clones above, but copy only checked-out files and submodule content
# into the seed context. Exact refs remain recorded in VENDOR-MANIFEST.txt.
[[ "$VENDOR_EXPORT_DIR" == "$OFFLINE_DIR/vendor-export" ]] || die "unsafe vendor export path"
rm -rf -- "$VENDOR_EXPORT_DIR"
mkdir -p "$VENDOR_EXPORT_DIR"
for item in "${vendors[@]}"; do
  IFS='|' read -r name _ _ <<<"$item"
  mkdir -p "$VENDOR_EXPORT_DIR/$name"
  tar --exclude-vcs --exclude='.dsv4-required-ref' \
    -C "$VENDOR_DIR/$name" -cf - . | tar -C "$VENDOR_EXPORT_DIR/$name" -xf -
done
cp "$MANIFEST" "$VENDOR_EXPORT_DIR/VENDOR-MANIFEST.txt"

log "materializing the cached base and Rust artifacts (no CUDA compilation)"
common_build_args=(
  --progress=plain --file "$SOURCE_DIR/docker/Dockerfile"
  --build-arg torch_cuda_arch_list=8.0
)
runtime build "${common_build_args[@]}" --target base --tag "$BASE_IMAGE" "$SOURCE_DIR" \
  2>&1 | tee "$REPORT_DIR/seed-base-image.log"
runtime build "${common_build_args[@]}" --target rust-build --tag "$RUST_IMAGE" "$SOURCE_DIR" \
  2>&1 | tee "$REPORT_DIR/seed-rust-image.log"

rm -f "$ARTIFACT_DIR"/vllm-rs "$ARTIFACT_DIR"/_rust_*.so
artifact_container="dsv4-seed-artifacts-$$"
runtime create --name "$artifact_container" \
  --label "com.deepseek.bundle=$BUNDLE_LABEL" "$RUST_IMAGE" >/dev/null
runtime cp "$artifact_container:/workspace/vllm/vllm-rs" "$ARTIFACT_DIR/vllm-rs"
rust_sos=$(runtime run --rm --network none --entrypoint bash "$RUST_IMAGE" -lc \
  "printf '%s\\n' /workspace/vllm/_rust_*.so")
while IFS= read -r so; do
  [[ "$so" == *'*'* ]] && continue
  runtime cp "$artifact_container:$so" "$ARTIFACT_DIR/$(basename "$so")"
done <<<"$rust_sos"
runtime rm "$artifact_container" >/dev/null
[[ -x "$ARTIFACT_DIR/vllm-rs" ]] || die "Rust executable extraction failed"
find "$ARTIFACT_DIR" -maxdepth 1 -name '_rust_*.so' -print -quit | grep -q . || \
  die "Rust Python extension extraction failed"

log "building the dependency-only seed image (no vLLM CUDA compilation)"
runtime build --progress=plain \
  --file "$OFFLINE_DIR/Dockerfile.seed" \
  --tag "$SEED_IMAGE" \
  --label "org.opencontainers.image.source=$VLLM_REPOSITORY" \
  --label "org.opencontainers.image.revision=$VLLM_COMMIT" \
  --label "com.deepseek.bundle=$BUNDLE_LABEL" \
  --label 'com.deepseek.image-kind=target-build-seed' \
  --build-arg "BUILD_BASE_IMAGE=$BASE_IMAGE" \
  --build-arg "PYPI_INDEX_URL=${BUILD_PIP_INDEX_URL:-https://pypi.tuna.tsinghua.edu.cn/simple}" \
  --build-context "vendor=$VENDOR_EXPORT_DIR" \
  --build-context "artifacts=$ARTIFACT_DIR" \
  --build-context "seedfiles=$SEED_FILES_DIR" \
  "$SOURCE_DIR" 2>&1 | tee "$REPORT_DIR/target-build-seed-image.log"

log "verifying the seed under --network none"
runtime run --rm --network none --entrypoint bash "$SEED_IMAGE" -lc '
set -e
test "$(git -C /opt/dsv4-source rev-parse HEAD)" = f8ea5bb163c161ef38b401d055cc5fd4a934091a
test -x /opt/offline-artifacts/vllm-rs
test -f /opt/vendor/VENDOR-MANIFEST.txt
for d in cutlass triton deepgemm msa flashmla flashkda qutlass tml-fa4 vllm-flash-attention; do test -d "/opt/vendor/$d"; done
python3 -c "import torch; print(torch.__version__, torch.version.cuda)"
nvcc --version
'
runtime image inspect "$SEED_IMAGE" >"$ROOT_DIR/common/manifests/target-build-seed-image-inspect.json"
runtime run --rm --network none --entrypoint python3 "$SEED_IMAGE" -m pip freeze --all \
  >"$ROOT_DIR/common/manifests/target-build-seed-pip-freeze.txt"
runtime run --rm --network none --entrypoint python3 "$SEED_IMAGE" -c '
import importlib.metadata as m, json
items=[]
for d in m.distributions():
    name=d.metadata.get("Name")
    if name:
        items.append({"name":name,"version":d.version,"license":d.metadata.get("License")})
print(json.dumps(sorted(items,key=lambda x:x["name"].lower()),indent=2))
' >"$ROOT_DIR/common/licenses/python-distribution-licenses.json"

log "exporting portable seed image archive"
rm -f "$SEED_ARCHIVE.tmp"
runtime save --output "$SEED_ARCHIVE.tmp" "$SEED_IMAGE"
mv -f "$SEED_ARCHIVE.tmp" "$SEED_ARCHIVE"
"$ROOT_DIR/scripts/update_checksums.sh"
printf 'TARGET_BUILD_SEED=PASS\narchive=%s\nsize_bytes=%s\n' \
  "$SEED_ARCHIVE" "$(stat -c %s "$SEED_ARCHIVE")"
