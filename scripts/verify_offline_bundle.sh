#!/usr/bin/env bash
set -euo pipefail

# shellcheck disable=SC1091
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib.sh"
load_mode_config target-only
artifact_only=0
if [[ "${1:-}" == --artifact-only ]]; then
  artifact_only=1
fi

failures=0
check() {
  local label=$1
  shift
  if "$@"; then
    printf 'PASS %s\n' "$label"
  else
    printf 'FAIL %s\n' "$label" >&2
    failures=$((failures + 1))
  fi
}

check "bundle SHA256" bash -c 'cd "$1" && sha256sum -c checksums/SHA256SUMS' _ "$ROOT_DIR"
check "fixed source commit manifest" grep -Fx "$VLLM_COMMIT" "$ROOT_DIR/common/manifests/source-commit.txt"
check "source archive exists" test -s "$ROOT_DIR/common/source/vllm-f8ea5bb-source.tar.gz"

archive=$(image_archive_path || true)
check "image archive exists" test -n "$archive"
if [[ "$archive" == *.zst ]]; then
  check "zstd image frame" zstd -q -t "$archive"
else
  check "tar image archive" tar -tf "$archive"
fi

if [[ -f "$ROOT_DIR/common/wheelhouse/WHEELHOUSE_COMPLETE" ]]; then
  check "wheelhouse lock" test -s "$ROOT_DIR/common/wheelhouse/requirements-lock.txt"
  wheel_count=$(find "$ROOT_DIR/common/wheelhouse" -maxdepth 2 -type f -name '*.whl' | wc -l)
  check "wheelhouse contains wheels" test "$wheel_count" -gt 0
else
  printf 'INFO native wheelhouse is not marked complete; container remains primary\n'
fi

if ((artifact_only)); then
  if ((failures)); then
    printf 'ARTIFACT_VERIFICATION=FAIL failures=%d\n' "$failures"
    exit 1
  fi
  printf 'ARTIFACT_VERIFICATION=PASS\n'
  exit 0
fi

detect_runtime
check "container runtime" runtime info
check "x86_64 architecture" test "$(uname -m)" = x86_64
check "Ubuntu 22.04 target" bash -c '. /etc/os-release; [[ "$ID" == ubuntu && "$VERSION_ID" == 22.04 ]]'
check "eight visible GPUs" bash -c '[[ $(nvidia-smi --query-gpu=index --format=csv,noheader 2>/dev/null | wc -l) == 8 ]]'
check "eight A100 GPUs" bash -c '[[ $(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | grep -c A100) == 8 ]]'

driver=$(nvidia-smi --query-gpu=driver_version --format=csv,noheader 2>/dev/null | head -n1 | tr -d ' ' || true)
check "driver >= 580.126.20" bash -c '[[ -n "$1" && "$(printf "%s\n%s\n" 580.126.20 "$1" | sort -V | head -n1)" == 580.126.20 ]]' _ "$driver"
check "model checkpoint metadata" python3 "$ROOT_DIR/scripts/verify_model.py" "$MODEL_DIR"

free_kib=$(df -Pk "$ROOT_DIR" | awk 'NR == 2 {print $4}')
archive_kib=$(( $(stat -c %s "$archive") / 1024 ))
check "disk space for image import" test "$free_kib" -gt "$archive_kib"

if ((failures)); then
  printf 'OFFLINE_BUNDLE_VERIFICATION=FAIL failures=%d\n' "$failures"
  exit 1
fi
printf 'OFFLINE_BUNDLE_VERIFICATION=PASS\n'
