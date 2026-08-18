#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
profile=${1:-}
case "$profile" in
  32k|128k|256k|1m) ;;
  *)
    printf 'Usage: %s {32k|128k|256k|1m}\n' "$0" >&2
    exit 2
    ;;
esac
mkdir -p "$ROOT_DIR/run"
printf '%s\n' "$profile" >"$ROOT_DIR/run/selected-profile"
printf 'Selected profile: %s\n' "$profile"
printf 'It takes effect on the next start. Existing containers are unchanged.\n'
