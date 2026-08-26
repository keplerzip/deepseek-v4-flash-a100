#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
"$SCRIPT_DIR/run_package_tests.sh"
"$SCRIPT_DIR/run_acceptance.sh"
printf 'R2_TESTS=PASS\nnext=run the full benchmark and 24-hour stability gate\n'
