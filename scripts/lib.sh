#!/usr/bin/env bash

# Shared helpers. This file is sourced; callers must enable their own strict mode.
ROOT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
RUN_DIR="$ROOT_DIR/run"
LOG_ROOT="$ROOT_DIR/logs"
REPORT_DIR="$ROOT_DIR/reports"
BUNDLE_LABEL=deepseek-v4-flash-a100-offline
RUNTIME_KIND=""
RUNTIME_CMD=()

log() {
  printf '[%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"
}

warn() {
  printf 'WARNING: %s\n' "$*" >&2
}

die() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

require_file() {
  [[ -f "$1" ]] || die "required file not found: $1"
}

load_mode_config() {
  local requested_mode=$1
  local caller_profile=${PROFILE:-}
  mkdir -p "$RUN_DIR" "$LOG_ROOT" "$REPORT_DIR"
  require_file "$ROOT_DIR/config/common.env"
  require_file "$ROOT_DIR/config/model.env"
  require_file "$ROOT_DIR/config/$requested_mode.env"
  # shellcheck disable=SC1090
  source "$ROOT_DIR/config/common.env"
  # shellcheck disable=SC1090
  source "$ROOT_DIR/config/model.env"
  # shellcheck disable=SC1090
  source "$ROOT_DIR/config/$requested_mode.env"

  if [[ -n "$caller_profile" ]]; then
    PROFILE=$caller_profile
  elif [[ -s "$RUN_DIR/selected-profile" ]]; then
    PROFILE=$(<"$RUN_DIR/selected-profile")
  fi
  [[ "$PROFILE" =~ ^(32k|128k|256k|1m)$ ]] || die "invalid PROFILE: $PROFILE"
  require_file "$ROOT_DIR/config/profiles/$PROFILE.env"
  # shellcheck disable=SC1090
  source "$ROOT_DIR/config/profiles/$PROFILE.env"

  case "$MODE" in
    target-only) MAX_NUM_SEQS=$TARGET_MAX_NUM_SEQS ;;
    dspark)
      MAX_NUM_SEQS=$DSPARK_MAX_NUM_SEQS
      SPECULATIVE_CONFIG=$(printf \
        '{"method":"%s","num_speculative_tokens":%s,"draft_sample_method":"%s"}' \
        "$DSPARK_METHOD" "$DSPARK_NUM_SPECULATIVE_TOKENS" \
        "$DSPARK_DRAFT_SAMPLE_METHOD")
      ;;
    *) die "unsupported mode in config: $MODE" ;;
  esac
  GPU_MEMORY_UTILIZATION=${GPU_MEMORY_UTILIZATION:-$MODE_GPU_MEMORY_UTILIZATION}
  MAX_NUM_BATCHED_TOKENS=${MAX_NUM_BATCHED_TOKENS:-$MODE_MAX_NUM_BATCHED_TOKENS}
  KV_CACHE_MEMORY_BYTES=${KV_CACHE_MEMORY_BYTES:-}
  [[ "$PORT" =~ ^[0-9]+$ ]] || die "PORT must be numeric: $PORT"
  [[ "$TENSOR_PARALLEL_SIZE" == 8 ]] || die "this bundle requires TP=8"
  [[ "$GPU_DEVICES" == "0,1,2,3,4,5,6,7" ]] || \
    die "this accepted bundle requires GPU_DEVICES=0,1,2,3,4,5,6,7"
  [[ "$EXECUTION_MODE" =~ ^(eager|graph)$ ]] || \
    die "EXECUTION_MODE must be eager or graph"
  [[ "$NETWORK_MODE" =~ ^(host|none)$ ]] || \
    die "NETWORK_MODE must be host or none"
  [[ "$GPU_MEMORY_UTILIZATION" =~ ^0\.[0-9]+$|^1\.0+$ ]] || \
    die "GPU_MEMORY_UTILIZATION must be in decimal form: $GPU_MEMORY_UTILIZATION"
  if [[ -n "$MAX_NUM_BATCHED_TOKENS" ]]; then
    [[ "$MAX_NUM_BATCHED_TOKENS" =~ ^[1-9][0-9]*$ ]] || \
      die "MAX_NUM_BATCHED_TOKENS must be a positive integer"
  fi
  if [[ -n "$KV_CACHE_MEMORY_BYTES" ]]; then
    [[ "$KV_CACHE_MEMORY_BYTES" =~ ^[1-9][0-9]*$ ]] || \
      die "KV_CACHE_MEMORY_BYTES must be a positive integer"
  fi
}

detect_runtime() {
  local requested=${CONTAINER_RUNTIME:-auto}
  if [[ "$requested" == auto || "$requested" == docker ]]; then
    if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
      RUNTIME_KIND=docker
      RUNTIME_CMD=(docker)
      return 0
    fi
    if command -v sudo >/dev/null 2>&1 && \
      sudo -n docker info >/dev/null 2>&1; then
      RUNTIME_KIND=docker
      RUNTIME_CMD=(sudo -n docker)
      return 0
    fi
  fi
  if [[ "$requested" == auto || "$requested" == podman ]]; then
    if command -v podman >/dev/null 2>&1 && podman info >/dev/null 2>&1; then
      RUNTIME_KIND=podman
      RUNTIME_CMD=(podman)
      return 0
    fi
  fi
  die "no usable container runtime; expected direct Docker, sudo -n Docker, or Podman"
}

runtime() {
  "${RUNTIME_CMD[@]}" "$@"
}

container_exists() {
  runtime container inspect "$1" >/dev/null 2>&1
}

container_running() {
  [[ "$(runtime inspect --format '{{.State.Running}}' "$1" 2>/dev/null || true)" == true ]]
}

assert_owned_container() {
  local name=$1
  local expected_mode=$2
  local bundle mode
  bundle=$(runtime inspect --format '{{index .Config.Labels "com.deepseek.bundle"}}' "$name" 2>/dev/null || true)
  mode=$(runtime inspect --format '{{index .Config.Labels "com.deepseek.mode"}}' "$name" 2>/dev/null || true)
  [[ "$bundle" == "$BUNDLE_LABEL" && "$mode" == "$expected_mode" ]] || \
    die "refusing to modify container $name: ownership labels do not match"
}

image_archive_path() {
  local zst="$ROOT_DIR/common/image/${IMAGE_ARCHIVE_BASENAME}.tar.zst"
  local tar="$ROOT_DIR/common/image/${IMAGE_ARCHIVE_BASENAME}.tar"
  if [[ -f "$zst" ]]; then
    printf '%s\n' "$zst"
  elif [[ -f "$tar" ]]; then
    printf '%s\n' "$tar"
  else
    return 1
  fi
}

image_present() {
  runtime image inspect "$IMAGE_NAME" >/dev/null 2>&1
}

write_launch_manifest() {
  local path=$1
  shift
  {
    printf 'generated_utc=%q\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf 'mode=%q\n' "$MODE"
    printf 'profile=%q\n' "$PROFILE"
    printf 'execution_mode=%q\n' "$EXECUTION_MODE"
    printf 'model_dir=%q\n' "$MODEL_DIR"
    printf 'image=%q\n' "$IMAGE_NAME"
    printf 'command='
    printf '%q ' "$@"
    printf '\n'
  } >"$path"
}

lock_owner_field() {
  local field=$1
  local lock_file="$RUN_DIR/dsv4-a100.lock"
  [[ -s "$lock_file" ]] || return 1
  sed -n "s/^${field}=//p" "$lock_file" | head -n1
}
