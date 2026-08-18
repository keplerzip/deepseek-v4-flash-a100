#!/usr/bin/env bash
# Source or execute this file to start the canonical target-only service:
#   source ./start-production.sh
# It intentionally leaves the selected variables exported for status and
# benchmark commands executed in the same shell.

_dsv4_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
# shellcheck disable=SC1091
source "$_dsv4_root/config/production-target.env"

export HOST="$DSV4_BIND_HOST"
export PORT PROFILE EXECUTION_MODE GPU_MEMORY_UTILIZATION
export MAX_NUM_BATCHED_TOKENS KV_CACHE_MEMORY_BYTES
export GPU_GUARD_ALLOWED_CONTAINERS FORCE_START

printf 'DSV4 production target: host=%s port=%s profile=%s graph=%s ' \
  "$HOST" "$PORT" "$PROFILE" "$EXECUTION_MODE"
printf 'gpu_memory=%s max_batched_tokens=%s max_num_seqs=16 tp=8\n' \
  "$GPU_MEMORY_UTILIZATION" "$MAX_NUM_BATCHED_TOKENS"

if [[ "${DSV4_CONFIG_ONLY:-0}" == 1 ]]; then
  printf 'DSV4_CONFIG_ONLY=1: configuration exported; service not started.\n'
else
  "$_dsv4_root/target-only/start.sh"
fi

unset _dsv4_root
