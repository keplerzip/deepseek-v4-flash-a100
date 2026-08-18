#!/usr/bin/env bash
set -euo pipefail

# shellcheck disable=SC1091
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib.sh"
mode=${1:-target-only}
load_mode_config "$mode"
detect_runtime

issues=0
printf 'GPU guard for mode=%s devices=%s port=%s\n' "$MODE" "$GPU_DEVICES" "$PORT"

container_is_allowed() {
  local candidate=$1 item
  local allowlist=${GPU_GUARD_ALLOWED_CONTAINERS:-}
  IFS=',' read -r -a allowed_items <<<"$allowlist"
  for item in "${allowed_items[@]}"; do
    item=${item#"${item%%[![:space:]]*}"}
    item=${item%"${item##*[![:space:]]}"}
    [[ -n "$item" && "$candidate" == "$item" ]] && return 0
  done
  return 1
}

if ! command -v nvidia-smi >/dev/null 2>&1; then
  printf 'BLOCK: nvidia-smi is not available.\n' >&2
  issues=$((issues + 1))
else
  gpu_inventory=$(nvidia-smi --query-gpu=index,name,memory.total,driver_version,compute_cap,uuid --format=csv,noheader 2>&1 || true)
  printf '%s\n' "$gpu_inventory"
  visible_count=$(printf '%s\n' "$gpu_inventory" | awk -F, '$1 ~ /^[[:space:]]*[0-7][[:space:]]*$/ {count++} END {print count+0}')
  a100_count=$(printf '%s\n' "$gpu_inventory" | awk -F, '$1 ~ /^[[:space:]]*[0-7][[:space:]]*$/ && $2 ~ /A100/ {count++} END {print count+0}')
  if [[ "$visible_count" != 8 || "$a100_count" != 8 ]]; then
    printf 'BLOCK: expected GPU indices 0-7 to be eight A100 devices.\n' >&2
    issues=$((issues + 1))
  fi

  process_rows=$(nvidia-smi --query-compute-apps=gpu_uuid,pid,process_name,used_gpu_memory --format=csv,noheader 2>/dev/null || true)
  if [[ -n "$process_rows" ]]; then
    printf 'BLOCK: existing NVIDIA compute processes were found:\n%s\n' "$process_rows" >&2
    while IFS=, read -r _ pid _; do
      pid=${pid//[[:space:]]/}
      if [[ "$pid" =~ ^[0-9]+$ ]]; then
        ps -p "$pid" -o user,pid,ppid,etimes,cmd --no-headers 2>/dev/null || true
      fi
    done <<<"$process_rows"
    issues=$((issues + 1))
  fi
fi

if command -v ss >/dev/null 2>&1 && \
  ss -H -ltnp 2>/dev/null | awk -v port=":$PORT" '$4 ~ port"$" {found=1} END {exit !found}'; then
  printf 'BLOCK: TCP port %s is already listening:\n' "$PORT" >&2
  ss -H -ltnp 2>/dev/null | awk -v port=":$PORT" '$4 ~ port"$"' >&2 || true
  issues=$((issues + 1))
fi

if [[ -s "$RUN_DIR/dsv4-a100.lock" ]]; then
  printf 'BLOCK: project lock exists: %s\n' "$RUN_DIR/dsv4-a100.lock" >&2
  sed 's/^/  /' "$RUN_DIR/dsv4-a100.lock" >&2
  issues=$((issues + 1))
fi

running_containers=$(runtime ps --format '{{.Names}}\t{{.Image}}\t{{.Status}}' 2>/dev/null || true)
if [[ -n "$running_containers" ]]; then
  printf 'Running containers (read only):\n%s\n' "$running_containers"
  while IFS=$'\t' read -r name _; do
    [[ -n "$name" ]] || continue
    device_requests=$(runtime inspect --format '{{json .HostConfig.DeviceRequests}}' "$name" 2>/dev/null || true)
    if [[ "$device_requests" == *gpu* || "$device_requests" == *nvidia* ]]; then
      if container_is_allowed "$name"; then
        printf 'ALLOW: listed monitoring GPU container: %s %s\n' "$name" "$device_requests"
      else
        printf 'BLOCK: running GPU container: %s %s\n' "$name" "$device_requests" >&2
        issues=$((issues + 1))
      fi
    fi
  done <<<"$running_containers"
fi

relevant_processes=$(ps -eo user,pid,ppid,etimes,cmd --sort=pid | \
  grep -Ei 'MiniMax|vllm|DeepSeek|ray::' | grep -v -E 'grep -E|gpu_guard.sh' || true)
if [[ -n "$relevant_processes" ]]; then
  printf 'Relevant host processes (informational; GPU/port checks decide blocking):\n%s\n' "$relevant_processes"
fi

if ((issues > 0)); then
  if [[ "${FORCE_START:-0}" == 1 ]]; then
    printf 'FORCE_START=1: bypassing %d guard issue(s). No process or container was killed.\n' "$issues" >&2
    exit 0
  fi
  printf 'GPU_GUARD=BLOCKED issues=%d\n' "$issues" >&2
  printf 'Nothing was stopped or modified. Set FORCE_START=1 only after manual review.\n' >&2
  exit 3
fi

printf 'GPU_GUARD=PASS\n'
