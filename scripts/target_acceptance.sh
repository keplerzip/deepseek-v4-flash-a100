#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
phase=${1:-preflight}
case "$phase" in
  preflight|target-only|dspark|all) ;;
  *) printf 'Usage: %s {preflight|target-only|dspark|all}\n' "$0" >&2; exit 2 ;;
esac

if [[ "$phase" == preflight ]]; then
  "$ROOT_DIR/scripts/inspect_environment.sh"
  if [[ -s "$ROOT_DIR/common/image/dsv4-a100-build-seed-f8ea5bb.tar" ]]; then
    "$ROOT_DIR/scripts/verify_target_build_seed.sh"
    # shellcheck disable=SC1091
    source "$ROOT_DIR/scripts/lib.sh"
    load_mode_config target-only
    detect_runtime
    [[ "$(uname -m)" == x86_64 ]]
    gpu_count=$(nvidia-smi --query-gpu=index --format=csv,noheader 2>/dev/null | wc -l)
    a100_count=$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | grep -c A100)
    [[ "$gpu_count" == 8 && "$a100_count" == 8 ]] || {
      printf 'Expected exactly 8 visible A100 GPUs; got gpu_count=%s a100_count=%s\n' "$gpu_count" "$a100_count" >&2
      exit 1
    }
    driver=$(nvidia-smi --query-gpu=driver_version --format=csv,noheader | head -n1 | tr -d ' ')
    [[ "$(printf '%s\n%s\n' 580.126.20 "$driver" | sort -V | head -n1)" == 580.126.20 ]] || {
      printf 'Driver %s is below required 580.126.20\n' "$driver" >&2
      exit 1
    }
    python3 "$ROOT_DIR/scripts/verify_model.py" "$MODEL_DIR"
    printf 'TARGET_PREFLIGHT=PASS\nNext: TARGET_BUILD_JOBS=16 NVCC_THREADS=1 ./scripts/build_on_target_offline.sh\n'
  else
    "$ROOT_DIR/scripts/verify_offline_bundle.sh"
    printf 'TARGET_PREFLIGHT=PASS\nNext: ./scripts/install_offline.sh\n'
  fi
  exit 0
fi

current=""
cleanup() {
  if [[ -n "$current" ]]; then
    "$ROOT_DIR/$current/stop.sh" || true
  fi
}
trap cleanup EXIT INT TERM

run_mode() {
  current=$1
  EXECUTION_MODE=eager PROFILE=32k "$ROOT_DIR/$current/start.sh"
  STRICT_FEATURES=1 "$ROOT_DIR/$current/smoke-test.sh"
  "$ROOT_DIR/$current/status.sh"
  "$ROOT_DIR/$current/stop.sh"
  current=""
}

if [[ "$phase" == target-only || "$phase" == all ]]; then run_mode target-only; fi
if [[ "$phase" == dspark || "$phase" == all ]]; then run_mode dspark; fi
trap - EXIT INT TERM
printf 'TARGET_ACCEPTANCE=PASS phase=%s\n' "$phase"
