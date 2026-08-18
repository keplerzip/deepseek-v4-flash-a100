#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
mode=${1:-}
case "$mode" in target-only|dspark) ;; *) printf 'Usage: %s {target-only|dspark}\n' "$0" >&2; exit 2 ;; esac
"$ROOT_DIR/$mode/smoke-test.sh"
"$ROOT_DIR/$mode/stop.sh"
"$ROOT_DIR/$mode/start.sh"
"$ROOT_DIR/$mode/smoke-test.sh"
printf 'RESTART_RECOVERY=PASS mode=%s\n' "$mode"
