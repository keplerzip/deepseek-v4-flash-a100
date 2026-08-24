#!/usr/bin/env bash
set -euo pipefail

# Developer/build-host entrypoint. The final target package never calls this.
# Compilation is fixed to four jobs and one NVCC thread per job.  The main
# vLLM target is SM80; the exact upstream wheel build also packages its
# mandatory architecture-specific auxiliary extensions (for example FA3).
# shellcheck disable=SC1091
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

export BASE_BUILD_CPU_QUOTA=400000
export BASE_BUILD_MAX_JOBS=4
export BASE_BUILD_NVCC_THREADS=1

"$R1_DIR/scripts/prepare_base_image.sh"
if docker_cmd image inspect "$R1_IMAGE" >/dev/null 2>&1; then
  verify_image_tree "$R1_IMAGE" r1-python.sha256
else
  "$R1_DIR/scripts/build_image.sh"
fi
"$R1_DIR/scripts/build_source_test_image.sh"
printf 'PRECOMPILED_IMAGES=PASS\nbase=%s\nruntime=%s\ntests=%s\n' \
  "$BASE_IMAGE" "$R1_IMAGE" "$SOURCE_TEST_IMAGE"
