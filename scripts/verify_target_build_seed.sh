#!/usr/bin/env bash
set -euo pipefail

# shellcheck disable=SC1091
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib.sh"
load_mode_config target-only
archive="$ROOT_DIR/common/image/dsv4-a100-build-seed-f8ea5bb.tar"
failures=0
check() {
  local label=$1; shift
  if "$@"; then printf 'PASS %s\n' "$label"; else printf 'FAIL %s\n' "$label" >&2; failures=$((failures + 1)); fi
}
check 'bundle SHA256' bash -c 'cd "$1" && sha256sum -c checksums/SHA256SUMS' _ "$ROOT_DIR"
check 'seed archive exists' test -s "$archive"
check 'seed archive is a Docker tar' bash -c 'tar -tf "$1" >/dev/null' _ "$archive"
check 'seed manifest exists' test -s "$ROOT_DIR/common/manifests/target-build-seed.txt"
check 'fixed vLLM commit' grep -Fq "vllm_commit=$VLLM_COMMIT" "$ROOT_DIR/common/manifests/target-build-seed.txt"
check 'seed image inspect manifest' test -s "$ROOT_DIR/common/manifests/target-build-seed-image-inspect.json"
check 'seed pip freeze manifest' test -s "$ROOT_DIR/common/manifests/target-build-seed-pip-freeze.txt"
check 'target build script' test -x "$ROOT_DIR/scripts/build_on_target_offline.sh"
check 'model path configured' bash -c '. "$1/config/model.env"; [[ "${MODEL_DIR%/}" == /ai/models/deepseek-v4-flash-0731-modelscope ]]' _ "$ROOT_DIR"
check 'target-only and dspark share port 8005' bash -c '. "$1/config/common.env"; [[ "$PORT" == 8005 ]]' _ "$ROOT_DIR"
check 'DSpark exact method' grep -Fq 'DSPARK_METHOD=dspark' "$ROOT_DIR/config/dspark.env"

if ((failures)); then
  printf 'TARGET_BUILD_SEED_VERIFICATION=FAIL failures=%d\n' "$failures"
  exit 1
fi
printf 'TARGET_BUILD_SEED_VERIFICATION=PASS\n'
