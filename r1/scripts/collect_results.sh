#!/usr/bin/env bash
set -euo pipefail

# Create a secret-free evidence bundle. The API key and secrets.env are never
# copied; container command evidence is pre-redacted by start.sh.
# shellcheck disable=SC1091
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

ensure_runtime_dirs
init_docker
require_command cp
require_command mktemp
require_command sha256sum
require_command tar

timestamp=$(date -u +%Y%m%dT%H%M%SZ)
staging=$(mktemp -d "$TMP_DIR/evidence.XXXXXX")
cleanup() {
  rm -rf -- "$staging"
}
trap cleanup EXIT

mkdir -p "$staging/runtime-results" "$staging/runtime-logs" \
  "$staging/release/manifests" "$staging/release/reports"
cp -a "$RESULT_DIR/." "$staging/runtime-results/"
cp -a "$LOG_DIR/." "$staging/runtime-logs/"
cp "$R1_DIR/config/target.env" "$staging/release/target.env"
cp "$PROFILE_CONFIG" "$staging/release/scheme-$SCHEME_ID.env"
cp -a "$R1_DIR/manifests/." "$staging/release/manifests/"
cp "$R1_DIR/reports/performance-report.artifact.json" \
  "$staging/release/reports/initial-performance-report.artifact.json"
cp "$R1_DIR/reports/performance-report.html" \
  "$staging/release/reports/initial-performance-report.html"
cp "$R1_DIR/reports/qa/delivery-receipt.json" \
  "$staging/release/reports/delivery-receipt.json"

for image in "$BASE_IMAGE" "$R1_IMAGE" "$SOURCE_TEST_IMAGE"; do
  if docker_cmd image inspect "$image" >/dev/null 2>&1; then
    safe_name=${image//[:\/]/_}
    docker_cmd image inspect "$image" \
      >"$staging/runtime-results/${safe_name}-image-inspect.json"
    docker_cmd image inspect --format '{{.Id}}' "$image" \
      >"$staging/runtime-results/${safe_name}-image-digest.txt"
    docker_cmd run --rm --network none \
      --env PIP_DISABLE_PIP_VERSION_CHECK=1 \
      --entrypoint python3 "$image" -m pip freeze --all \
      >"$staging/runtime-results/${safe_name}-pip-freeze.txt"
  fi
done
if docker_cmd image inspect "$R1_IMAGE" >/dev/null 2>&1; then
  docker_cmd run --rm --network none --gpus all \
    --entrypoint nvidia-smi "$R1_IMAGE" -q \
    >"$staging/runtime-results/nvidia-smi-q.txt" 2>&1 || \
    warn "GPU evidence could not be captured"
fi

mkdir -p "$R1_DIR/results"
bundle="$R1_DIR/results/dsv4-target-$SCHEME_ID-evidence-$timestamp.tar.gz"
tar -czf "$bundle" -C "$staging" .
sha256sum "$bundle" >"$bundle.sha256"
printf 'EVIDENCE_BUNDLE=PASS\nbundle=%s\nchecksum=%s.sha256\n' \
  "$bundle" "$bundle"
