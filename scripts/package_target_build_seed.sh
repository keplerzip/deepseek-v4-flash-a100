#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
PARENT_DIR=$(dirname -- "$ROOT_DIR")
BUNDLE_NAME=$(basename -- "$ROOT_DIR")
OUTPUT=${1:-"$PARENT_DIR/deepseek-v4-flash-a100-target-build-seed-f8ea5bb-ubuntu22.04-x86_64.tar.xz"}
temporary="$OUTPUT.tmp"
command -v xz >/dev/null 2>&1 || {
  printf 'xz is required (Ubuntu package: xz-utils)\n' >&2
  exit 1
}
# XZ preset 6 is the normal compression mode: materially faster and less
# memory-hungry than -9e, while remaining directly extractable by Ubuntu 22.04.
# PACKAGE_JOBS controls compression only; target compilation has its own
# stricter TARGET_BUILD_JOBS limit.
compress_program="xz -6 -T${PACKAGE_JOBS:-8}"

"$ROOT_DIR/scripts/update_checksums.sh"
"$ROOT_DIR/scripts/verify_target_build_seed.sh"
rm -f "$temporary"
tar \
  --exclude="$BUNDLE_NAME/common/source/vllm-build" \
  --exclude="$BUNDLE_NAME/common/offline-build/vendor-src" \
  --exclude="$BUNDLE_NAME/common/offline-build/vendor-export" \
  --exclude="$BUNDLE_NAME/common/offline-build/artifacts" \
  --exclude="$BUNDLE_NAME/common/target-build-output" \
  --exclude="$BUNDLE_NAME/run/*" \
  --exclude="$BUNDLE_NAME/logs/*" \
  --exclude="$BUNDLE_NAME/benchmarks/results/*" \
  --exclude='*/__pycache__' --exclude='*.pyc' \
  -C "$PARENT_DIR" --use-compress-program="$compress_program" \
  -cf "$temporary" "$BUNDLE_NAME"
mv -f "$temporary" "$OUTPUT"
xz -t "$OUTPUT"
tar -tJf "$OUTPUT" | grep -Fx "$BUNDLE_NAME/scripts/build_on_target_offline.sh" >/dev/null
sha256sum "$OUTPUT" >"$OUTPUT.sha256"
printf 'PACKAGE=%s\nSHA256=%s\nSIZE_BYTES=%s\n' \
  "$OUTPUT" "$OUTPUT.sha256" "$(stat -c %s "$OUTPUT")"
