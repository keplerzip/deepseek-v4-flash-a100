#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
mode=${1:-one}

case "$mode" in
  one | target) start_script=start_one.sh ;;
  two | dspark) start_script=start_two.sh ;;
  --build-only) start_script= ;;
  *)
    printf 'usage: %s [one|two|--build-only]\n' "$0" >&2
    exit 2
    ;;
esac

"$ROOT_DIR/r2/incremental/install.sh"
[[ -z "$start_script" ]] || exec "$ROOT_DIR/$start_script"
