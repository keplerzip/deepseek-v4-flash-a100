#!/usr/bin/env bash
set -euo pipefail

R1_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
# Used by scripts that source this library.
# shellcheck disable=SC2034
ROOT_DIR=$(cd -- "$R1_DIR/.." && pwd)

case "${DSV4_SCHEME:-one}" in
  one | two) ;;
  *)
    printf '[dsv4-r1] ERROR: DSV4_SCHEME must be one or two\n' >&2
    exit 1
    ;;
esac
SELECTED_SCHEME=${DSV4_SCHEME:-one}

set -a
# shellcheck disable=SC1091
source "$R1_DIR/config/target.env"
PROFILE_CONFIG="$R1_DIR/config/schemes/$SELECTED_SCHEME.env"
if [[ -f "$R1_DIR/config/secrets.env" ]]; then
  # shellcheck disable=SC1091
  source "$R1_DIR/config/secrets.env"
fi
# Source the scheme last so secrets may configure credentials and paths but
# cannot alter the locked GPU, TP, scheduler, container, or benchmark contract.
# shellcheck disable=SC1090
source "$PROFILE_CONFIG"
DSV4_SCHEME=$SCHEME_ID
# Used by scripts that source this library.
# shellcheck disable=SC2034
SCHEME_ONE_CONTAINER_NAME=dsv4-target-r1-20260820
# shellcheck disable=SC2034
SCHEME_TWO_CONTAINER_NAME=dsv4-target-r1-two-20260820
# Capture the selected profile contract for explicit preflight diagnostics.
# Used by scripts that source this library.
# shellcheck disable=SC2034
PROFILE_GPU_DEVICES=$GPU_DEVICES
# shellcheck disable=SC2034
PROFILE_GPU_COUNT=$GPU_COUNT
# shellcheck disable=SC2034
PROFILE_TENSOR_PARALLEL_SIZE=$TENSOR_PARALLEL_SIZE
# shellcheck disable=SC2034
PROFILE_MAX_NUM_SEQS=$MAX_NUM_SEQS
# shellcheck disable=SC2034
PROFILE_BENCHMARK_MAX_CONCURRENCY=$BENCHMARK_MAX_CONCURRENCY
RUNTIME_ROOT=$RUNTIME_BASE/$SCHEME_ID
set +a

LOG_DIR="$RUNTIME_ROOT/logs"
RUN_DIR="$RUNTIME_ROOT/run"
CACHE_DIR="$RUNTIME_ROOT/cache"
TMP_DIR="$RUNTIME_ROOT/tmp"
RESULT_DIR="$RUNTIME_ROOT/results"
CONTROL_DIR="$RUNTIME_BASE/control"
OWNER_LABEL="deepseek-v4-flash-a100-target-r1"
DSV4_DOCKER_CMD=()

log() {
  printf '[dsv4-r1:%s] %s\n' "$SCHEME_ID" "$*"
}

warn() {
  printf '[dsv4-r1:%s] WARNING: %s\n' "$SCHEME_ID" "$*" >&2
}

die() {
  printf '[dsv4-r1:%s] ERROR: %s\n' "$SCHEME_ID" "$*" >&2
  exit 1
}

ensure_runtime_dirs() {
  mkdir -p \
    "$LOG_DIR" "$RUN_DIR" "$CACHE_DIR" "$TMP_DIR" "$RESULT_DIR" "$CONTROL_DIR"
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || die "required command is missing: $1"
}

init_docker() {
  ((${#DSV4_DOCKER_CMD[@]})) && return 0
  if [[ "${DSV4_FORCE_SUDO_DOCKER:-0}" == 1 ]]; then
    command -v sudo >/dev/null 2>&1 || die "sudo is unavailable"
    sudo -n docker version >/dev/null 2>&1 || die \
      "DSV4_FORCE_SUDO_DOCKER=1 but sudo -n docker is unavailable"
    DSV4_DOCKER_CMD=(sudo -n docker)
  elif command -v docker >/dev/null 2>&1 \
    && docker version >/dev/null 2>&1; then
    DSV4_DOCKER_CMD=(docker)
  elif command -v sudo >/dev/null 2>&1 \
    && sudo -n docker version >/dev/null 2>&1; then
    DSV4_DOCKER_CMD=(sudo -n docker)
  else
    die "Docker is unavailable; this release requires docker or sudo -n docker"
  fi
}

docker_cmd() {
  init_docker
  "${DSV4_DOCKER_CMD[@]}" "$@"
}

api_url() {
  printf 'http://%s:%s%s' "$API_PROBE_HOST" "$PORT" "$1"
}

curl_auth_args=()
if [[ -n "${DSV4_API_KEY:-}" ]]; then
  curl_auth_args=(-H "Authorization: Bearer $DSV4_API_KEY")
fi

container_exists() {
  docker_cmd container inspect "$1" >/dev/null 2>&1
}

container_running() {
  [[ "$(docker_cmd container inspect --format '{{.State.Running}}' "$1" 2>/dev/null)" == true ]]
}

assert_owned_container() {
  local container=$1
  local observed
  observed=$(docker_cmd container inspect --format \
    '{{index .Config.Labels "com.deepseek.owner"}}' "$container" 2>/dev/null || true)
  [[ "$observed" == "$OWNER_LABEL" ]] || die \
    "container is not owned by this release: $container"
}

verify_image_tree() {
  local image=$1
  local manifest=$2
  docker_cmd run --rm --network none \
    --volume "$R1_DIR:/audit:ro" \
    --entrypoint python3 "$image" \
    /audit/scripts/verify_installed_tree.py \
    --manifest "/audit/manifests/$manifest"
}

safe_container_state() {
  local container=$1
  docker_cmd inspect --format \
    '{"id":"{{.Id}}","restart_count":{{.RestartCount}},"started_at":"{{.State.StartedAt}}","status":"{{.State.Status}}","oom_killed":{{.State.OOMKilled}}}' \
    "$container"
}

capture_container_state() {
  local container=$1
  local output_dir=$2
  local output_name=$3
  local target
  output_dir=$(cd -- "$output_dir" && pwd)
  target="$output_dir/$output_name"
  if safe_container_state "$container" >"$target"; then
    return 0
  fi
  printf '%s\n' \
    '{"id":null,"restart_count":0,"started_at":null,"status":"unavailable","oom_killed":false,"capture_error":"container_inspect_failed"}' \
    >"$target"
  return 1
}

snapshot_engine_processes() {
  local container=$1
  local output_dir=$2
  local output_name=$3
  local host_user
  local target
  host_user="$(id -u):$(id -g)"
  output_dir=$(cd -- "$output_dir" && pwd)
  target="$output_dir/$output_name"
  if docker_cmd run --rm --network none \
    --pid "container:$container" \
    --user "$host_user" \
    --volume "$R1_DIR:/audit:ro" \
    --volume "$output_dir:/runtime-evidence:rw" \
    --entrypoint python3 "$R1_IMAGE" \
    /audit/tests/process_snapshot.py \
    --output "/runtime-evidence/$output_name"; then
    return 0
  fi
  if [[ ! -s "$target" ]]; then
    printf '%s\n' \
      '{"status":"fail","engine_core_processes":[],"vllm_processes":[],"capture_error":"pid_namespace_snapshot_failed"}' \
      >"$target"
  fi
  return 1
}

finalize_runtime_evidence() {
  local output_dir=$1
  local summary_name=$2
  local harness_status=$3
  local host_user
  host_user="$(id -u):$(id -g)"
  output_dir=$(cd -- "$output_dir" && pwd)
  docker_cmd run --rm --network none \
    --user "$host_user" \
    --volume "$R1_DIR:/audit:ro" \
    --volume "$output_dir:/runtime-evidence:rw" \
    --entrypoint python3 "$R1_IMAGE" \
    /audit/tests/finalize_stability_evidence.py \
    --summary "/runtime-evidence/$summary_name" \
    --process-before /runtime-evidence/process-before.json \
    --process-after /runtime-evidence/process-after.json \
    --container-before /runtime-evidence/container-before.json \
    --container-after /runtime-evidence/container-after.json \
    --harness-exit "$harness_status"
}

http_ready() {
  local selected_running=0
  local selected_container
  for selected_container in \
    "$CONTAINER_NAME" "${CONTAINER_NAME}-base-rollback"; do
    if container_running "$selected_container"; then
      selected_running=1
      break
    fi
  done
  ((selected_running)) || return 1
  if command -v curl >/dev/null 2>&1; then
    curl --noproxy '*' --fail --silent --show-error --max-time 5 \
      "${curl_auth_args[@]}" "$(api_url /v1/models)" >/dev/null
    return
  fi
  local container
  for container in "$CONTAINER_NAME" "${CONTAINER_NAME}-base-rollback"; do
    container_running "$container" || continue
    docker_cmd exec \
      --env "DSV4_PROBE_KEY=${DSV4_API_KEY:-}" \
      "$container" python3 -c '
import os
import urllib.request
request = urllib.request.Request("http://127.0.0.1:'"$PORT"'/v1/models")
if key := os.environ.get("DSV4_PROBE_KEY"):
    request.add_header("Authorization", f"Bearer {key}")
with urllib.request.urlopen(request, timeout=5) as response:
    raise SystemExit(0 if response.status == 200 else 1)
' >/dev/null
    return
  done
  return 1
}

print_models() {
  if command -v curl >/dev/null 2>&1; then
    curl --noproxy '*' --fail --silent --show-error \
      "${curl_auth_args[@]}" "$(api_url /v1/models)"
    return
  fi
  local container
  for container in "$CONTAINER_NAME" "${CONTAINER_NAME}-base-rollback"; do
    container_running "$container" || continue
    docker_cmd exec \
      --env "DSV4_PROBE_KEY=${DSV4_API_KEY:-}" \
      "$container" python3 -c '
import os
import urllib.request
request = urllib.request.Request("http://127.0.0.1:'"$PORT"'/v1/models")
if key := os.environ.get("DSV4_PROBE_KEY"):
    request.add_header("Authorization", f"Bearer {key}")
with urllib.request.urlopen(request, timeout=5) as response:
    print(response.read().decode())
'
    return
  done
  return 1
}
