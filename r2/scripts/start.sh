#!/usr/bin/env bash
set -euo pipefail

# shellcheck disable=SC1091
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

ensure_runtime_dirs
require_command flock
init_docker
exec 9>"$CONTROL_DIR/start-stop.lock"
flock -n 9 || die 'another R2 start/stop operation is in progress'

stop_other_r2_services
stop_known_r1_services
if container_exists "$CONTAINER_NAME"; then
  assert_owned_container "$CONTAINER_NAME"
  container_running "$CONTAINER_NAME" && die \
    "owned container is already running: $CONTAINER_NAME"
  log "removing stopped release-owned container: $CONTAINER_NAME"
  docker_cmd container rm "$CONTAINER_NAME" >/dev/null
fi
"$R2_DIR/scripts/preflight.sh"

docker_bridge_gateway=$(docker_cmd network inspect "$NETWORK_MODE" \
  --format '{{(index .IPAM.Config 0).Gateway}}')
[[ "$docker_bridge_gateway" =~ ^([0-9]{1,3}[.]){3}[0-9]{1,3}$ ]] || die \
  "could not resolve Docker bridge gateway: $docker_bridge_gateway"

vllm_args=(
  "$CONTAINER_MODEL_DIR"
  --tensor-parallel-size "$TENSOR_PARALLEL_SIZE"
  --trust-remote-code
  --kv-cache-dtype "$KV_CACHE_DTYPE"
  --block-size "$BLOCK_SIZE"
  --tokenizer-mode deepseek_v4
  --tool-call-parser deepseek_v4
  --enable-auto-tool-choice
  --reasoning-parser deepseek_v4
  --served-model-name "${SERVED_MODEL_NAMES[@]}"
  --host "$HOST"
  --port "$PORT"
  --max-model-len "$MAX_MODEL_LEN"
  --max-num-seqs "$MAX_NUM_SEQS"
  --max-num-batched-tokens "$MAX_NUM_BATCHED_TOKENS"
  --gpu-memory-utilization "$GPU_MEMORY_UTILIZATION"
  --enable-prefix-caching
  --prefix-caching-hash-algo sha256
  --enable-tokenizer-info-endpoint
  --enable-prompt-tokens-details
  --enable-force-include-usage
  --enable-per-request-metrics
)
if [[ "$SCHEME_ID" == dspark ]]; then
  speculative_config=$(printf \
    '{"method":"dspark","num_speculative_tokens":%s,"draft_sample_method":"greedy"}' \
    "$DSPARK_K")
  vllm_args+=(--speculative-config "$speculative_config")
else
  for argument in "${vllm_args[@]}"; do
    [[ "$argument" != --speculative-config* ]] || die \
      'target safety invariant rejected speculative decoding'
  done
fi
if [[ "$EXECUTION_MODE" == eager ]]; then
  vllm_args+=(--enforce-eager)
elif [[ "$EXECUTION_MODE" != graph ]]; then
  die 'EXECUTION_MODE must be graph or eager'
fi
if [[ -n "${DSV4_API_KEY:-}" ]]; then
  vllm_args+=(--api-key "$DSV4_API_KEY")
fi

retention_env=()
if [[ -n "$PREFIX_CACHE_RETENTION_INTERVAL" ]]; then
  retention_env=(--env \
    "VLLM_PREFIX_CACHE_RETENTION_INTERVAL=$PREFIX_CACHE_RETENTION_INTERVAL")
fi

launch_file="$RESULT_DIR/launch.argv"
{
  printf '%q ' "${DSV4_DOCKER_CMD[@]}"
  printf '%q ' run --detach --name "$CONTAINER_NAME"
  redact=0
  for argument in "${vllm_args[@]}"; do
    if ((redact)); then printf '%q ' '<redacted>'; redact=0
    else
      printf '%q ' "$argument"
      [[ "$argument" != --api-key ]] || redact=1
    fi
  done
  printf '\n'
} >"$launch_file"

container_id=$(docker_cmd run --detach \
  --name "$CONTAINER_NAME" \
  --label "com.deepseek.owner=$OWNER_LABEL" \
  --label "com.deepseek.release=$R2_RELEASE" \
  --label "com.deepseek.scheme=$SCHEME_ID" \
  --label "com.deepseek.dspark-k=$DSPARK_K" \
  --label "com.deepseek.cache-profile=$PREFIX_CACHE_PROFILE" \
  --label com.deepseek.role=inference \
  --network "$NETWORK_MODE" \
  --publish "$HOST_PUBLISH_ADDRESS:$PORT:$PORT" \
  --publish "$docker_bridge_gateway:$PORT:$PORT" \
  --gpus all \
  --shm-size "$SHM_SIZE" \
  --ulimit memlock=-1:-1 \
  --ulimit stack=67108864:67108864 \
  --mount "type=bind,src=$MODEL_DIR,dst=$CONTAINER_MODEL_DIR,readonly" \
  --volume "$CACHE_DIR:/runtime-cache:rw" \
  --volume "$TMP_DIR:/runtime-tmp:rw" \
  --env "CUDA_VISIBLE_DEVICES=$GPU_DEVICES" \
  --env CUDA_DEVICE_ORDER=PCI_BUS_ID \
  --env "HF_HUB_OFFLINE=$HF_HUB_OFFLINE" \
  --env "TRANSFORMERS_OFFLINE=$TRANSFORMERS_OFFLINE" \
  --env "HF_DATASETS_OFFLINE=$HF_DATASETS_OFFLINE" \
  --env "VLLM_NO_USAGE_STATS=$VLLM_NO_USAGE_STATS" \
  --env "DO_NOT_TRACK=$DO_NOT_TRACK" \
  --env "TOKENIZERS_PARALLELISM=$TOKENIZERS_PARALLELISM" \
  --env "VLLM_SERVED_MODEL_MAX_LENS=$SERVED_MODEL_MAX_LENS" \
  "${retention_env[@]}" \
  --env VLLM_SPARSE_DENSE_QUERY_BLOCK=8 \
  --env HF_HOME=/runtime-cache/huggingface \
  --env TORCH_HOME=/runtime-cache/torch \
  --env TRITON_CACHE_DIR=/runtime-cache/triton \
  --env XDG_CACHE_HOME=/runtime-cache/xdg \
  --env TMPDIR=/runtime-tmp \
  --env NCCL_DEBUG=WARN \
  "$R2_IMAGE" "${vllm_args[@]}")
printf '%s\n' "$container_id" >"$RUN_DIR/container-id"

deadline=$((SECONDS + STARTUP_TIMEOUT_SECONDS))
while ! http_ready; do
  if ! container_running "$CONTAINER_NAME"; then
    docker_cmd logs --timestamps "$CONTAINER_NAME" >"$LOG_DIR/startup-failed.log" 2>&1 || true
    die "container exited during startup; see $LOG_DIR/startup-failed.log"
  fi
  ((SECONDS < deadline)) || {
    docker_cmd logs --timestamps "$CONTAINER_NAME" >"$LOG_DIR/startup-timeout.log" 2>&1 || true
    die 'API startup timed out; container was left running for diagnosis'
  }
  log "waiting for $(api_url /v1/models)"
  sleep "$HEALTH_POLL_SECONDS"
done

docker_cmd inspect --format \
  '{"id":"{{.Id}}","image":"{{.Image}}","started":"{{.State.StartedAt}}","status":"{{.State.Status}}","network":"{{.HostConfig.NetworkMode}}","ports":{{json .HostConfig.PortBindings}}}' \
  "$CONTAINER_NAME" >"$RESULT_DIR/container-safe-inspect.json"
docker_cmd logs --timestamps "$CONTAINER_NAME" >"$LOG_DIR/startup.log" 2>&1 || true
printf 'SERVICE_START=PASS\nscheme=%s\ncontainer=%s\nurl=%s/v1\nmodels=%s\nmax_model_len=%s\nmax_num_seqs=%s\ncache_profile=%s\n' \
  "$SCHEME_ID" "$CONTAINER_NAME" "$(api_url '')" \
  "${SERVED_MODEL_NAMES[*]}" "$MAX_MODEL_LEN" "$MAX_NUM_SEQS" \
  "$PREFIX_CACHE_PROFILE"
