#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
PARENT_DIR=$(dirname -- "$ROOT_DIR")
BUNDLE_NAME=$(basename -- "$ROOT_DIR")
VERSION=$(<"$ROOT_DIR/VERSION")
OUTPUT=${1:-"$PARENT_DIR/${BUNDLE_NAME}-${VERSION}-ubuntu22.04-x86_64.tar.gz"}
temporary="$OUTPUT.tmp"
compress_program=gzip
if command -v pigz >/dev/null 2>&1; then
  compress_program="pigz -p ${PACKAGE_JOBS:-8}"
fi

"$ROOT_DIR/scripts/update_checksums.sh"
"$ROOT_DIR/scripts/verify_offline_bundle.sh" --artifact-only

rm -f "$temporary"
tar \
  --exclude="$BUNDLE_NAME/common/source/vllm-build" \
  --exclude="$BUNDLE_NAME/run/*" \
  --exclude="$BUNDLE_NAME/logs/*" \
  --exclude="$BUNDLE_NAME/benchmarks/results/*" \
  --exclude='*/__pycache__' \
  --exclude='*.pyc' \
  -C "$PARENT_DIR" --use-compress-program="$compress_program" \
  -cf "$temporary" "$BUNDLE_NAME"
mv -f "$temporary" "$OUTPUT"
gzip -t "$OUTPUT"
tar -tzf "$OUTPUT" | grep -Fx "$BUNDLE_NAME/README.md" >/dev/null
sha256sum "$OUTPUT" >"$OUTPUT.sha256"
printf 'PACKAGE=%s\nSHA256=%s\nSIZE_BYTES=%s\n' \
  "$OUTPUT" "$OUTPUT.sha256" "$(stat -c %s "$OUTPUT")"
