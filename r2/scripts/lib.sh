#!/usr/bin/env bash
set -euo pipefail

R2_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
ROOT_DIR=$(cd -- "$R2_DIR/.." && pwd)

case "${DSV4_SCHEME:-target}" in
  target | dspark) SELECTED_SCHEME=${DSV4_SCHEME:-target} ;;
  one) SELECTED_SCHEME=target ;;
  two) SELECTED_SCHEME=dspark ;;
  *)
    printf '[dsv4-r2] ERROR: DSV4_SCHEME must be target or dspark\n' >&2
    exit 1
    ;;
esac

set -a
# shellcheck disable=SC1091
source "$R2_DIR/config/common.env"
if [[ -f "$R2_DIR/config/secrets.env" ]]; then
  # shellcheck disable=SC1091
  source "$R2_DIR/config/secrets.env"
fi
# The scheme is sourced last so credentials and paths are configurable while
# GPU count, TP layout and speculative method remain release-owned.
# shellcheck disable=SC1090
source "$R2_DIR/config/schemes/$SELECTED_SCHEME.env"
# Release-owned values are deliberately loaded last. A copied secrets.env may
# configure local paths and credentials, but cannot mutate TP8/C16, aliases,
# image provenance, context limits or the Docker-only publication boundary.
# shellcheck disable=SC1091
source "$R2_DIR/config/release.env"
DSV4_SCHEME=$SCHEME_ID
set +a

case "$PREFIX_CACHE_PROFILE" in
  legacy) PREFIX_CACHE_RETENTION_INTERVAL= ;;
  zero) PREFIX_CACHE_RETENTION_INTERVAL=0 ;;
  32768) PREFIX_CACHE_RETENTION_INTERVAL=32768 ;;
  *)
    printf '[dsv4-r2:%s] ERROR: PREFIX_CACHE_PROFILE must be legacy, zero, or 32768\n' \
      "$SCHEME_ID" >&2
    exit 1
    ;;
esac
if [[ "$SCHEME_ID" == dspark ]]; then
  case "$DSPARK_K" in 1 | 3 | 5 | 7) ;; *)
    printf '[dsv4-r2:dspark] ERROR: DSV4_DSPARK_K must be 1, 3, 5, or 7\n' >&2
    exit 1
  esac
fi

declare -ar SERVED_MODEL_NAMES=(
  'deepseek-v4-flash'
  'deepseek-v4-flash[1M]'
  'deepseek-v4-flash-claude'
  'deepseek-v4-flash-claude[1M]'
)
readonly OWNER_LABEL=deepseek-v4-flash-a100-r2
readonly R2_IMAGE R2_SOURCE_COMMIT R2_RELEASE MIN_NVIDIA_DRIVER
readonly CONTAINER_MODEL_DIR MAX_MODEL_LEN SHORT_MODEL_MAX_LEN MAX_NUM_SEQS
readonly MAX_NUM_BATCHED_TOKENS KV_CACHE_DTYPE BLOCK_SIZE SERVED_MODEL_MAX_LENS
readonly HOST PORT HOST_PUBLISH_ADDRESS API_PROBE_HOST NETWORK_MODE
readonly GPU_DEVICES GPU_COUNT TENSOR_PARALLEL_SIZE
readonly HF_HUB_OFFLINE TRANSFORMERS_OFFLINE HF_DATASETS_OFFLINE
readonly VLLM_NO_USAGE_STATS DO_NOT_TRACK TOKENIZERS_PARALLELISM
readonly SCHEME_ID SCHEME_LABEL CONTAINER_NAME SPECULATIVE_METHOD DSPARK_K
readonly REPORT_PORT_DEFAULT REPORT_CONTAINER_NAME DSV4_SCHEME
runtime_suffix=$SCHEME_ID
if [[ "$SCHEME_ID" == dspark ]]; then
  runtime_suffix="$SCHEME_ID-k$DSPARK_K"
fi
RUNTIME_ROOT="$RUNTIME_BASE/$runtime_suffix"
LOG_DIR="$RUNTIME_ROOT/logs"
RUN_DIR="$RUNTIME_ROOT/run"
CACHE_DIR="$RUNTIME_ROOT/cache"
TMP_DIR="$RUNTIME_ROOT/tmp"
RESULT_DIR="$RUNTIME_ROOT/results"
CONTROL_DIR="$RUNTIME_BASE/control"
DSV4_DOCKER_CMD=()

log() { printf '[dsv4-r2:%s] %s\n' "$runtime_suffix" "$*"; }
warn() { printf '[dsv4-r2:%s] WARNING: %s\n' "$runtime_suffix" "$*" >&2; }
die() { printf '[dsv4-r2:%s] ERROR: %s\n' "$runtime_suffix" "$*" >&2; exit 1; }

assert_release_contract() {
  [[ "$R2_RELEASE" == 2026.08.30-r2.3 ]] || die 'release identity is corrupt'
  [[ "$MAX_MODEL_LEN" == 1048576 && "$SHORT_MODEL_MAX_LEN" == 262144 ]] || die \
    'context contract is corrupt'
  [[ "$MAX_NUM_SEQS" == 16 && "$MAX_NUM_BATCHED_TOKENS" == 4096 ]] || die \
    'scheduler contract is corrupt'
  [[ "$GPU_DEVICES" == 0,1,2,3,4,5,6,7 && "$GPU_COUNT" == 8 && \
        "$TENSOR_PARALLEL_SIZE" == 8 ]] || die 'TP8 GPU contract is corrupt'
  [[ "$HOST" == 0.0.0.0 && "$PORT" == 8005 && \
        "$HOST_PUBLISH_ADDRESS" == 127.0.0.1 && "$NETWORK_MODE" == bridge ]] || die \
    'network boundary contract is corrupt'
  [[ "${#SERVED_MODEL_NAMES[@]}" == 4 && \
        "${SERVED_MODEL_NAMES[0]}" == deepseek-v4-flash && \
        "${SERVED_MODEL_NAMES[1]}" == 'deepseek-v4-flash[1M]' && \
        "${SERVED_MODEL_NAMES[2]}" == deepseek-v4-flash-claude && \
        "${SERVED_MODEL_NAMES[3]}" == 'deepseek-v4-flash-claude[1M]' ]] || die \
    'served-model alias contract is corrupt'
}

assert_release_contract

ensure_runtime_dirs() {
  mkdir -p "$LOG_DIR" "$RUN_DIR" "$CACHE_DIR" "$TMP_DIR" \
    "$RESULT_DIR" "$CONTROL_DIR"
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || die "required command is missing: $1"
}

init_docker() {
  ((${#DSV4_DOCKER_CMD[@]})) && return 0
  if command -v docker >/dev/null 2>&1 && docker version >/dev/null 2>&1; then
    DSV4_DOCKER_CMD=(docker)
  elif command -v sudo >/dev/null 2>&1 && sudo -n docker version >/dev/null 2>&1; then
    DSV4_DOCKER_CMD=(sudo -n docker)
  else
    die 'Docker is unavailable; this release requires docker or sudo -n docker'
  fi
}

docker_cmd() { init_docker; "${DSV4_DOCKER_CMD[@]}" "$@"; }
container_exists() { docker_cmd container inspect "$1" >/dev/null 2>&1; }
container_running() {
  [[ "$(docker_cmd container inspect --format '{{.State.Running}}' "$1" 2>/dev/null)" == true ]]
}

assert_owned_container() {
  local container=$1 observed
  observed=$(docker_cmd container inspect --format \
    '{{index .Config.Labels "com.deepseek.owner"}}' "$container" 2>/dev/null || true)
  [[ "$observed" == "$OWNER_LABEL" ]] || die \
    "container is not owned by this release: $container"
}

api_url() { printf 'http://%s:%s%s' "$API_PROBE_HOST" "$PORT" "$1"; }

http_ready() {
  container_running "$CONTAINER_NAME" || return 1
  if command -v curl >/dev/null 2>&1; then
    local auth=()
    [[ -z "${DSV4_API_KEY:-}" ]] || auth=(-H "Authorization: Bearer $DSV4_API_KEY")
    curl --noproxy '*' --fail --silent --show-error --max-time 5 \
      "${auth[@]}" "$(api_url /v1/models)" >/dev/null
    return
  fi
  docker_cmd exec --env "DSV4_PROBE_KEY=${DSV4_API_KEY:-}" "$CONTAINER_NAME" \
    python3 -c '
import os, urllib.request
r=urllib.request.Request("http://127.0.0.1:'"$PORT"'/v1/models")
if key := os.environ.get("DSV4_PROBE_KEY"): r.add_header("Authorization", f"Bearer {key}")
with urllib.request.urlopen(r, timeout=5) as response:
    raise SystemExit(0 if response.status == 200 else 1)
' >/dev/null
}

stop_other_r2_services() {
  local name
  while IFS= read -r name; do
    [[ -n "$name" && "$name" != "$CONTAINER_NAME" ]] || continue
    if container_running "$name"; then
      log "stopping alternate R2 service: $name"
      docker_cmd stop --time 120 "$name" >/dev/null
    fi
  done < <(docker_cmd ps -a --filter "label=com.deepseek.owner=$OWNER_LABEL" \
    --format '{{.Names}}')
}

stop_known_r1_services() {
  local name owner
  for name in dsv4-target-r1-20260820 dsv4-target-r1-two-20260820; do
    container_running "$name" || continue
    owner=$(docker_cmd inspect --format \
      '{{index .Config.Labels "com.deepseek.owner"}}' "$name" 2>/dev/null || true)
    if [[ "$owner" == deepseek-v4-flash-a100-target-r1 ]]; then
      log "stopping label-verified R1 service: $name"
      docker_cmd stop --time 120 "$name" >/dev/null
    else
      die "known R1 name is running without its expected ownership label: $name"
    fi
  done
}
