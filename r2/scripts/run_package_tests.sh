#!/usr/bin/env bash
set -euo pipefail
# shellcheck disable=SC1091
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib.sh"
"$R2_DIR/scripts/load_image.sh"

mapfile -t shell_files < <(find "$R2_DIR" -type f -name '*.sh' -print | sort)
for shell_file in "${shell_files[@]}"; do bash -n "$shell_file"; done

docker_cmd run --rm --network none \
  --volume "$ROOT_DIR:/bundle:ro" \
  --env PYTHONPYCACHEPREFIX=/tmp/pycache \
  --entrypoint python3 "$R2_IMAGE" -m py_compile \
  /bundle/r2/benchmarks/long_context_matrix.py \
  /bundle/r2/benchmarks/select_cache_profile.py \
  /bundle/r2/benchmarks/select_dspark_k.py \
  /bundle/r2/benchmarks/compare_schemes.py \
  /bundle/r2/benchmarks/stability_soak.py \
  /bundle/r2/reports/generate_report.py \
  /bundle/r2/scripts/verify_model.py \
  /bundle/r2/tests/api_contract.py \
  /bundle/r2/tests/test_package_contract.py

docker_cmd run --rm --network none \
  --volume "$ROOT_DIR:/bundle:ro" \
  --workdir /bundle \
  --entrypoint python3 "$R2_IMAGE" \
  r2/tests/test_package_contract.py

docker_cmd run --rm --network none \
  --volume "$R2_DIR:/r2:ro" \
  --entrypoint python3 "$R2_IMAGE" \
  /r2/benchmarks/long_context_matrix.py \
  --scheme target --cache-profile zero --output /tmp/matrix-plan.csv --plan-only
printf 'PACKAGE_TESTS=PASS\nshell_files=%s\npython_contract=pass\nmatrix_cells=60\n' \
  "${#shell_files[@]}"
