#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)

# Validate the immutable base, GPUs, checkpoint, and known tokenizer signature
# before stopping the healthy R1 container.  --skip-port permits that container
# to keep serving while the rollback candidate is checked.
"$SCRIPT_DIR/preflight.sh" --base --skip-port
"$SCRIPT_DIR/stop.sh"
DSV4_LAUNCH_MODE=base "$SCRIPT_DIR/start.sh"
printf 'ROLLBACK=PASS\nbase_tag=dsv4-a100:1281004-base\n'
