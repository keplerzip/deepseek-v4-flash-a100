#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
OUTPUT="$ROOT_DIR/checksums/SHA256SUMS"
temporary="$OUTPUT.tmp"
mkdir -p "$ROOT_DIR/checksums"

cd "$ROOT_DIR"
find \
  README.md CODEX-CLI-GUIDE.md CHANGELOG.md BLOCKERS.md VERSION UPDATE-MANIFEST.txt \
  start-production.sh stop-production.sh docs config common target-only dspark scripts benchmarks reports \
  -type f \
  ! -path 'common/image/*.tar' \
  ! -path 'common/image/*.tar.zst' \
  ! -path 'common/source/vllm-build/*' \
  ! -path 'common/offline-build/vendor-src/*' \
  ! -path 'common/offline-build/vendor-export/*' \
  ! -path 'common/offline-build/artifacts/*' \
  ! -path 'common/target-build-output/*' \
  ! -path 'benchmarks/results/*' \
  ! -path 'logs/*' \
  ! -path 'run/*' \
  ! -path '*/__pycache__/*' \
  ! -name '*.pyc' \
  ! -name '*.partial.jsonl' \
  -print0 | sort -z | xargs -0 sha256sum >"$temporary"

# A working tree can retain both the older build seed and the compiled runtime.
# A transferable bundle contains exactly one large Docker archive, preferring
# the final runtime. Hash only the artifact that package/install logic will use.
runtime_tar=common/image/dsv4-a100-vllm-f8ea5bb.tar
runtime_zst=${runtime_tar}.zst
seed_tar=common/image/dsv4-a100-build-seed-f8ea5bb.tar
if [[ -s "$runtime_zst" ]]; then
  sha256sum "$runtime_zst" >>"$temporary"
elif [[ -s "$runtime_tar" ]]; then
  sha256sum "$runtime_tar" >>"$temporary"
elif [[ -s "$seed_tar" ]]; then
  sha256sum "$seed_tar" >>"$temporary"
fi
mv -f "$temporary" "$OUTPUT"
printf 'Wrote %s (%s files)\n' "$OUTPUT" "$(wc -l <"$OUTPUT")"
