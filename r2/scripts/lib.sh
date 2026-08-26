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

SERVED_MODEL_NAMES=(
  'deepseek-v4-flash'
  'deepseek-v4-flash[1M]'
  'deepseek-v4-flash-claude'
  'deepseek-v4-flash-claude[1M]'
)
OWNER_LABEL=deepseek-v4-flash-a100-r2
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
