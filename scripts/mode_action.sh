#!/usr/bin/env bash
set -euo pipefail

# shellcheck disable=SC1091
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

requested_mode=${1:-}
action=${2:-}
shift 2 || true
case "$requested_mode" in target-only|dspark) ;; *) die "mode must be target-only or dspark" ;; esac
load_mode_config "$requested_mode"
detect_runtime

MODE_LOG_DIR="$LOG_ROOT/$MODE"
LOCK_FILE="$RUN_DIR/dsv4-a100.lock"
GUARD_FILE="$RUN_DIR/dsv4-a100.guard"
CONTAINER_ID_FILE="$RUN_DIR/$MODE.container-id"
PID_FILE="$RUN_DIR/$MODE.pid"
LAUNCH_FILE="$RUN_DIR/$MODE.launch"
STARTUP_FILE="$RUN_DIR/$MODE.startup.env"
mkdir -p "$MODE_LOG_DIR" "$RUN_DIR/cache/$MODE" "$RUN_DIR/tmp/$MODE"

http_ready() {
  if [[ "$NETWORK_MODE" == none ]] && container_exists "$CONTAINER_NAME"; then
    runtime exec "$CONTAINER_NAME" python3 - "$PORT" <<'PY'
import sys
import urllib.request
with urllib.request.urlopen(
    f"http://127.0.0.1:{sys.argv[1]}/v1/models", timeout=5
) as response:
    raise SystemExit(0 if response.status == 200 else 1)
PY
    return
  fi
  if command -v curl >/dev/null 2>&1; then
    curl --noproxy '*' -fsS --max-time 5 "http://$HOST:$PORT/v1/models" >/dev/null
  else
    python3 - "$HOST" "$PORT" <<'PY'
import sys
import urllib.request
with urllib.request.urlopen(
    f"http://{sys.argv[1]}:{sys.argv[2]}/v1/models", timeout=5
) as response:
    raise SystemExit(0 if response.status == 200 else 1)
PY
  fi
}

save_logs() {
  local suffix=${1:-latest}
  if container_exists "$CONTAINER_NAME"; then
    runtime logs --timestamps "$CONTAINER_NAME" \
      >"$MODE_LOG_DIR/${suffix}.log" 2>&1 || true
  fi
}

write_lock() {
  local container_id=$1
  local temporary="$LOCK_FILE.tmp.$$"
  {
    printf 'mode=%s\n' "$MODE"
    printf 'container=%s\n' "$CONTAINER_NAME"
    printf 'container_id=%s\n' "$container_id"
    printf 'port=%s\n' "$PORT"
    printf 'gpu_devices=%s\n' "$GPU_DEVICES"
    printf 'created_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  } >"$temporary"
  mv -f "$temporary" "$LOCK_FILE"
}

clear_own_lock() {
  [[ -s "$LOCK_FILE" ]] || return 0
  local owner_mode owner_container
  owner_mode=$(lock_owner_field mode || true)
  owner_container=$(lock_owner_field container || true)
  if [[ "$owner_mode" == "$MODE" && "$owner_container" == "$CONTAINER_NAME" ]]; then
    rm -f "$LOCK_FILE"
  else
    die "lock belongs to mode=${owner_mode:-unknown} container=${owner_container:-unknown}; refusing to clear it"
  fi
}

start_service() {
  [[ "$MODEL_DIR" == /* ]] || die "MODEL_DIR must be an absolute path: $MODEL_DIR"
  [[ -d "$MODEL_DIR" ]] || die "MODEL_DIR does not exist: $MODEL_DIR"
  image_present || die "image is not imported: $IMAGE_NAME; run scripts/install_offline.sh"

  "$ROOT_DIR/scripts/verify_image.sh"
  if [[ "$SKIP_MODEL_VERIFY" != 1 ]]; then
    local model_report="$REPORT_DIR/model-verification-$(date -u +%Y%m%dT%H%M%SZ).json"
    python3 "$ROOT_DIR/scripts/verify_model.py" "$MODEL_DIR" --json-output "$model_report"
  else
    warn "SKIP_MODEL_VERIFY=1; checkpoint file integrity was not checked"
  fi

  "$ROOT_DIR/scripts/gpu_guard.sh" "$MODE"
  command -v flock >/dev/null 2>&1 || die "flock is required (Ubuntu package util-linux)"
  exec 9>"$GUARD_FILE"
  flock -n 9 || die "another bundle start/stop operation is in progress"

  [[ ! -s "$LOCK_FILE" ]] || die "common lock already exists: $LOCK_FILE"
  if container_exists "$CONTAINER_NAME"; then
    assert_owned_container "$CONTAINER_NAME" "$MODE"
    if container_running "$CONTAINER_NAME"; then
      die "owned container is already running: $CONTAINER_NAME"
    fi
    log "removing stopped container owned by this mode: $CONTAINER_NAME"
    runtime rm "$CONTAINER_NAME" >/dev/null
  fi

  local vllm_args=(
    "$CONTAINER_MODEL_DIR"
    --tensor-parallel-size "$TENSOR_PARALLEL_SIZE"
    --trust-remote-code
    --kv-cache-dtype fp8
    --block-size 256
    --tokenizer-mode deepseek_v4
    --tool-call-parser deepseek_v4
    --enable-auto-tool-choice
    --reasoning-parser deepseek_v4
    --served-model-name "$SERVED_MODEL_NAME"
    --host "$HOST"
    --port "$PORT"
    --max-model-len "$MAX_MODEL_LEN"
    --max-num-seqs "$MAX_NUM_SEQS"
    --gpu-memory-utilization "$GPU_MEMORY_UTILIZATION"
  )
  if [[ -n "$MAX_NUM_BATCHED_TOKENS" ]]; then
    vllm_args+=(--max-num-batched-tokens "$MAX_NUM_BATCHED_TOKENS")
  fi
  if [[ -n "$KV_CACHE_MEMORY_BYTES" ]]; then
    vllm_args+=(--kv-cache-memory-bytes "$KV_CACHE_MEMORY_BYTES")
  fi
  if [[ "$EXECUTION_MODE" == eager ]]; then
    vllm_args+=(--enforce-eager)
  fi
  if [[ "$MODE" == dspark ]]; then
    [[ "$DSPARK_METHOD" == dspark ]] || die "DSpark method must be exactly dspark"
    [[ "$DSPARK_DRAFT_SAMPLE_METHOD" == greedy ]] || \
      die "baseline DSpark draft_sample_method must be greedy"
    vllm_args+=(--speculative-config "$SPECULATIVE_CONFIG")
  fi
  if [[ -n "$EXTRA_VLLM_ARGS" ]]; then
    local extra_args=()
    read -r -a extra_args <<<"$EXTRA_VLLM_ARGS"
    vllm_args+=("${extra_args[@]}")
  fi

  if [[ "$MODE" == target-only ]]; then
    local item
    for item in "${vllm_args[@]}"; do
      [[ "$item" != --speculative-config* ]] || \
        die "internal safety check failed: target-only contains speculative config"
    done
  fi

  local gpu_args=()
  if [[ "$RUNTIME_KIND" == docker ]]; then
    # This bundle requires all eight GPUs. Docker 29 rejects the unquoted
    # comma-separated device request with "cannot set both Count and
    # DeviceIDs"; --gpus all is unambiguous, while CUDA_VISIBLE_DEVICES below
    # still fixes the in-container ordering to 0-7.
    gpu_args=(--gpus all)
  else
    gpu_args=(--device nvidia.com/gpu=all)
  fi
  local runtime_args=(
    run --detach
    --name "$CONTAINER_NAME"
    --label "com.deepseek.bundle=$BUNDLE_LABEL"
    --label "com.deepseek.mode=$MODE"
    --label "com.deepseek.vllm.commit=$VLLM_COMMIT"
    --network "$NETWORK_MODE"
    --shm-size "$SHM_SIZE"
    --ulimit memlock=-1:-1
    --ulimit stack=67108864:67108864
    --volume "$MODEL_DIR:$CONTAINER_MODEL_DIR:ro"
    --volume "$RUN_DIR/cache/$MODE:/runtime-cache:rw"
    --volume "$RUN_DIR/tmp/$MODE:/runtime-tmp:rw"
    --volume "$ROOT_DIR/scripts:/bundle-scripts:ro"
    --env "CUDA_VISIBLE_DEVICES=$GPU_DEVICES"
    --env CUDA_DEVICE_ORDER=PCI_BUS_ID
    --env "HF_HUB_OFFLINE=$HF_HUB_OFFLINE"
    --env "TRANSFORMERS_OFFLINE=$TRANSFORMERS_OFFLINE"
    --env "HF_DATASETS_OFFLINE=$HF_DATASETS_OFFLINE"
    --env "VLLM_NO_USAGE_STATS=$VLLM_NO_USAGE_STATS"
    --env "DO_NOT_TRACK=$DO_NOT_TRACK"
    --env "TOKENIZERS_PARALLELISM=$TOKENIZERS_PARALLELISM"
    --env HF_HOME=/runtime-cache/huggingface
    --env TORCH_HOME=/runtime-cache/torch
    --env TRITON_CACHE_DIR=/runtime-cache/triton
    --env XDG_CACHE_HOME=/runtime-cache/xdg
    --env TMPDIR=/runtime-tmp
    --env NCCL_DEBUG=WARN
    "${gpu_args[@]}"
    "$IMAGE_NAME"
    "${vllm_args[@]}"
  )

  write_launch_manifest "$LAUNCH_FILE" "${RUNTIME_CMD[@]}" "${runtime_args[@]}"
  log "starting $MODE with profile=$PROFILE execution=$EXECUTION_MODE at $HOST:$PORT"
  local container_id launched_epoch
  launched_epoch=$(date +%s)
  if ! container_id=$(runtime "${runtime_args[@]}"); then
    die "container launch failed; common lock was not created"
  fi
  printf '%s\n' "$container_id" >"$CONTAINER_ID_FILE"
  write_lock "$container_id"
  runtime inspect --format '{{.State.Pid}}' "$CONTAINER_NAME" >"$PID_FILE"

  local started deadline now
  started=$launched_epoch
  deadline=$((started + STARTUP_TIMEOUT_SECONDS))
  while true; do
    if ! container_running "$CONTAINER_NAME"; then
      save_logs "failed-$(date -u +%Y%m%dT%H%M%SZ)"
      runtime ps -a --filter "name=^/${CONTAINER_NAME}$" || true
      die "container exited during startup; inspect $MODE_LOG_DIR and run $requested_mode/stop.sh"
    fi
    if http_ready; then
      break
    fi
    now=$(date +%s)
    if ((now >= deadline)); then
      save_logs "startup-timeout-$(date -u +%Y%m%dT%H%M%SZ)"
      die "startup timed out after $STARTUP_TIMEOUT_SECONDS seconds; container was left running for inspection"
    fi
    log "waiting for /v1/models ($((now - started))s elapsed)"
    sleep "$HEALTH_POLL_SECONDS"
  done
  local ready_epoch
  ready_epoch=$(date +%s)
  {
    printf 'mode=%s\n' "$MODE"
    printf 'profile=%s\n' "$PROFILE"
    printf 'execution_mode=%s\n' "$EXECUTION_MODE"
    printf 'container_launch_epoch_s=%s\n' "$launched_epoch"
    printf 'api_ready_epoch_s=%s\n' "$ready_epoch"
    printf 'startup_to_api_ready_s=%s\n' "$((ready_epoch - launched_epoch))"
  } >"$STARTUP_FILE"
  save_logs latest

  if [[ "$MODE" == dspark ]]; then
    local dspark_log
    dspark_log=$(runtime logs "$CONTAINER_NAME" 2>&1 || true)
    if ! grep -Eiq 'dspark' <<<"$dspark_log"; then
      warn "API is healthy but DSpark was not found in current logs; acceptance is not complete"
    fi
    if grep -Eiq 'method[=: ]+mtp|Hopper-only|Blackwell-only|no kernel image|invalid device function' <<<"$dspark_log"; then
      warn "DSpark log contains a forbidden/fatal pattern; inspect logs before testing"
      return 4
    fi
  fi

  if [[ "$NETWORK_MODE" == none ]]; then
    log "SERVICE_READY mode=$MODE network=none; API is reachable only inside the container"
  else
    log "SERVICE_READY mode=$MODE endpoint=http://$HOST:$PORT/v1 model=$SERVED_MODEL_NAME"
    log "Next: $ROOT_DIR/$MODE/smoke-test.sh"
  fi
}

stop_service() {
  command -v flock >/dev/null 2>&1 || die "flock is required"
  exec 9>"$GUARD_FILE"
  flock -n 9 || die "another bundle start/stop operation is in progress"

  if ! container_exists "$CONTAINER_NAME"; then
    warn "owned container does not exist: $CONTAINER_NAME"
    if [[ -s "$LOCK_FILE" ]]; then
      clear_own_lock
      log "cleared stale lock for absent owned container"
    fi
    rm -f "$CONTAINER_ID_FILE" "$PID_FILE"
    return 0
  fi
  assert_owned_container "$CONTAINER_NAME" "$MODE"
  save_logs "before-stop-$(date -u +%Y%m%dT%H%M%SZ)"
  if container_running "$CONTAINER_NAME"; then
    log "stopping owned container $CONTAINER_NAME"
    runtime stop --time 120 "$CONTAINER_NAME" >/dev/null
  fi
  save_logs "final-$(date -u +%Y%m%dT%H%M%SZ)"
  runtime rm "$CONTAINER_NAME" >/dev/null
  clear_own_lock
  rm -f "$CONTAINER_ID_FILE" "$PID_FILE"
  log "stopped and removed only the owned $MODE container; model, image and other services were untouched"
}

status_service() {
  printf 'mode=%s\nprofile=%s\nexecution_mode=%s\nendpoint=http://%s:%s/v1\n' \
    "$MODE" "$PROFILE" "$EXECUTION_MODE" "$HOST" "$PORT"
  printf 'max_model_len=%s\nmax_num_seqs=%s\ngpu_memory_utilization=%s\n' \
    "$MAX_MODEL_LEN" "$MAX_NUM_SEQS" "$GPU_MEMORY_UTILIZATION"
  printf 'max_num_batched_tokens=%s\nkv_cache_memory_bytes=%s\n' \
    "${MAX_NUM_BATCHED_TOKENS:-auto}" "${KV_CACHE_MEMORY_BYTES:-auto}"
  if [[ -s "$LOCK_FILE" ]]; then
    printf 'common_lock:\n'
    sed 's/^/  /' "$LOCK_FILE"
  else
    printf 'common_lock=absent\n'
  fi
  if ! container_exists "$CONTAINER_NAME"; then
    printf 'container=%s absent\n' "$CONTAINER_NAME"
    return 3
  fi
  assert_owned_container "$CONTAINER_NAME" "$MODE"
  runtime ps -a --filter "name=^/${CONTAINER_NAME}$" \
    --format 'container={{.Names}} image={{.Image}} status={{.Status}}'
  runtime inspect --format 'host_pid={{.State.Pid}} running={{.State.Running}} started={{.State.StartedAt}}' "$CONTAINER_NAME"
  if http_ready; then
    printf 'api_health=PASS\n'
  else
    printf 'api_health=FAIL\n'
  fi
  if command -v nvidia-smi >/dev/null 2>&1; then
    nvidia-smi --query-gpu=index,memory.used,memory.total,utilization.gpu,power.draw --format=csv,noheader || true
  fi
  if [[ "$MODE" == dspark ]]; then
    if command -v curl >/dev/null 2>&1; then
      curl --noproxy '*' -fsS "http://$HOST:$PORT/metrics" 2>/dev/null | \
        grep -E 'vllm:spec_decode_num_(drafts|draft_tokens|accepted_tokens)' || true
    fi
    runtime logs --tail 2000 "$CONTAINER_NAME" 2>&1 | \
      grep -Ei 'dspark|Mean acceptance|Draft acceptance|speculative' | tail -30 || true
  fi
}

show_logs() {
  container_exists "$CONTAINER_NAME" || die "container does not exist: $CONTAINER_NAME"
  assert_owned_container "$CONTAINER_NAME" "$MODE"
  save_logs latest
  if (($#)); then
    runtime logs "$@" "$CONTAINER_NAME"
  else
    runtime logs --tail 200 "$CONTAINER_NAME"
  fi
}

case "$action" in
  start) start_service "$@" ;;
  stop) stop_service "$@" ;;
  status) status_service "$@" ;;
  logs) show_logs "$@" ;;
  *) die "action must be start, stop, status, or logs" ;;
esac
