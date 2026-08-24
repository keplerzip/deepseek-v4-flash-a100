#!/usr/bin/env bash
set -euo pipefail

# One-command, resumable benchmark and report flow for the selected scheme.
# shellcheck disable=SC1091
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

"$R1_DIR/scripts/load_images.sh"
if container_running "$CONTAINER_NAME"; then
  assert_owned_container "$CONTAINER_NAME"
  http_ready || die "selected scheme container is running but its API is not ready"
else
  "$R1_DIR/scripts/start.sh"
fi
"$R1_DIR/scripts/run_performance.sh" "$@"
"$R1_DIR/scripts/generate_result_artifact.sh"
printf 'BENCHMARK=PASS\nscheme=%s\nmatrix=%s\nreport=%s\n' \
  "$SCHEME_ID" \
  "$RESULT_DIR/performance-$SCHEME_ID/performance-matrix.csv" \
  "$RESULT_DIR/performance-$SCHEME_ID/performance-report.html"
