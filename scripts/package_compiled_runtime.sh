#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
PARENT_DIR=$(dirname -- "$ROOT_DIR")
BUNDLE_NAME=$(basename -- "$ROOT_DIR")
IMAGE_ARCHIVE="$ROOT_DIR/common/image/dsv4-a100-vllm-f8ea5bb.tar"
OUTPUT=${1:-"$PARENT_DIR/deepseek-v4-flash-a100-runtime-f8ea5bb-ubuntu22.04-x86_64.tar.xz"}
temporary="$OUTPUT.tmp"
jobs=${PACKAGE_JOBS:-16}

[[ "$jobs" =~ ^[1-9][0-9]*$ ]] || {
  printf 'PACKAGE_JOBS must be a positive integer: %s\n' "$jobs" >&2
  exit 2
}
command -v xz >/dev/null 2>&1 || {
  printf 'xz is required (Ubuntu package: xz-utils)\n' >&2
  exit 1
}
[[ -s "$IMAGE_ARCHIVE" ]] || {
  printf 'compiled runtime archive is missing: %s\n' "$IMAGE_ARCHIVE" >&2
  exit 1
}

"$ROOT_DIR/scripts/update_checksums.sh"
"$ROOT_DIR/scripts/verify_offline_bundle.sh" --artifact-only

rm -f "$temporary"
tar \
  --exclude="$BUNDLE_NAME/common/image/dsv4-a100-build-seed-f8ea5bb.tar" \
  --exclude="$BUNDLE_NAME/common/source/vllm-build" \
  --exclude="$BUNDLE_NAME/common/offline-build/vendor-src" \
  --exclude="$BUNDLE_NAME/common/offline-build/vendor-export" \
  --exclude="$BUNDLE_NAME/common/offline-build/artifacts" \
  --exclude="$BUNDLE_NAME/common/target-build-output" \
  --exclude="$BUNDLE_NAME/run/*" \
  --exclude="$BUNDLE_NAME/logs/*" \
  --exclude="$BUNDLE_NAME/benchmarks/results/*" \
  --exclude='*/__pycache__' --exclude='*.pyc' \
  -C "$PARENT_DIR" --use-compress-program="xz -6 -T$jobs" \
  -cf "$temporary" "$BUNDLE_NAME"
mv -f "$temporary" "$OUTPUT"

xz -t "$OUTPUT"
tar -tJf "$OUTPUT" | grep -Fx "$BUNDLE_NAME/README.md" >/dev/null
tar -tJf "$OUTPUT" | grep -Fx \
  "$BUNDLE_NAME/common/image/dsv4-a100-vllm-f8ea5bb.tar" >/dev/null
if tar -tJf "$OUTPUT" | grep -Fq \
  "$BUNDLE_NAME/common/image/dsv4-a100-build-seed-f8ea5bb.tar"; then
  printf 'compiled package unexpectedly contains the redundant seed archive\n' >&2
  exit 1
fi
sha256sum "$OUTPUT" >"$OUTPUT.sha256"
printf 'PACKAGE=%s\nSHA256=%s\nSIZE_BYTES=%s\nPACKAGE_JOBS=%s\n' \
  "$OUTPUT" "$OUTPUT.sha256" "$(stat -c %s "$OUTPUT")" "$jobs"
