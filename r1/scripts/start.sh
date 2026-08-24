#!/usr/bin/env bash
set -euo pipefail

# shellcheck disable=SC1091
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

launch_mode=${DSV4_LAUNCH_MODE:-r1}
launch_image=$R1_IMAGE
launch_container=$CONTAINER_NAME
launch_manifest=r1-python.sha256
if [[ "$launch_mode" == base ]]; then
  launch_image=$BASE_IMAGE
  launch_container="${CONTAINER_NAME}-base-rollback"
  launch_manifest=base-python.sha256
elif [[ "$launch_mode" != r1 ]]; then
  die "DSV4_LAUNCH_MODE must be r1 or base"
fi

ensure_runtime_dirs
require_command flock
init_docker
exec 9>"$CONTROL_DIR/start-stop.lock"
flock -n 9 || die "another start/stop operation is in progress for $SCHEME_LABEL"
"$R1_DIR/scripts/stop_legacy_containers.sh"

# Schemes overlap on GPUs 4-7 and expose the same API port.  A requested switch
# therefore stops only containers carrying this release's ownership label.  It
# retains those containers and all of their per-scheme evidence.
for alternate in \
  "$ALTERNATE_CONTAINER_NAME" \
  "${ALTERNATE_CONTAINER_NAME}-base-rollback"; do
  container_exists "$alternate" || continue
  assert_owned_container "$alternate"
  if container_running "$alternate"; then
    log "stopping the alternate scheme before switching: $alternate"
    alternate_log_dir=$RUNTIME_BASE/one/logs
    if [[ "$alternate" == "$SCHEME_TWO_CONTAINER_NAME"* ]]; then
      alternate_log_dir=$RUNTIME_BASE/two/logs
    fi
    mkdir -p "$alternate_log_dir"
    docker_cmd logs --timestamps "$alternate" \
      >"$alternate_log_dir/${alternate}-scheme-switch-$(date -u +%Y%m%dT%H%M%SZ).log" \
      2>&1 || true
    docker_cmd stop --time 120 "$alternate" >/dev/null
  fi
done

other_container=$CONTAINER_NAME
if [[ "$launch_mode" == r1 ]]; then
  other_container="${CONTAINER_NAME}-base-rollback"
fi
if container_exists "$other_container" && container_running "$other_container"; then
  assert_owned_container "$other_container"
  die "the alternate release container is running; stop it first: $other_container"
fi
if [[ "$launch_mode" == base ]]; then
  "$R1_DIR/scripts/preflight.sh" --base
else
  "$R1_DIR/scripts/preflight.sh"
fi
docker_bridge_gateway=$(docker_cmd network inspect "$NETWORK_MODE" \
  --format '{{(index .IPAM.Config 0).Gateway}}')
[[ "$docker_bridge_gateway" =~ ^([0-9]{1,3}[.]){3}[0-9]{1,3}$ ]] || die \
  "could not resolve the Docker bridge gateway: $docker_bridge_gateway"
if container_exists "$launch_container"; then
  assert_owned_container "$launch_container"
  container_running "$launch_container" && die \
    "owned container is already running: $launch_container"
  log "removing the stopped container owned by this release: $launch_container"
  docker_cmd container rm "$launch_container" >/dev/null
fi

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
  --served-model-name "$SERVED_MODEL_NAME" "$CLAUDE_MODEL_ALIAS"
  --host "$HOST"
  --port "$PORT"
  --max-model-len "$MAX_MODEL_LEN"
  --max-num-seqs "$MAX_NUM_SEQS"
  --max-num-batched-tokens "$MAX_NUM_BATCHED_TOKENS"
  --gpu-memory-utilization "$GPU_MEMORY_UTILIZATION"
  --enable-tokenizer-info-endpoint
  --enable-prompt-tokens-details
  --enable-force-include-usage
  --enable-per-request-metrics
)
if [[ "$EXECUTION_MODE" == eager ]]; then
  vllm_args+=(--enforce-eager)
elif [[ "$EXECUTION_MODE" != graph ]]; then
  die "EXECUTION_MODE must be graph or eager"
fi
if [[ -n "${DSV4_API_KEY:-}" ]]; then
  vllm_args+=(--api-key "$DSV4_API_KEY")
fi
for argument in "${vllm_args[@]}"; do
  [[ "$argument" != --speculative-config* ]] || die \
    "target-only safety invariant rejected speculative decoding"
done

launch_file="$RESULT_DIR/${launch_mode}-launch.argv"
{
  printf '%q ' "${DSV4_DOCKER_CMD[@]}"
  printf ' %q' run --detach --name "$launch_container" "$launch_image"
  redact_next=0
  for argument in "${vllm_args[@]}"; do
    if ((redact_next)); then
      printf ' %q' '<redacted>'
      redact_next=0
    else
      printf ' %q' "$argument"
      if [[ "$argument" == --api-key ]]; then
        redact_next=1
      fi
    fi
  done
  printf '\n'
} >"$launch_file"

container_id=$(docker_cmd run --detach \
  --name "$launch_container" \
  --label "com.deepseek.owner=$OWNER_LABEL" \
  --label "com.deepseek.release=$R1_RELEASE" \
  --label "com.deepseek.scheme=$SCHEME_ID" \
  --label "com.deepseek.launch-mode=$launch_mode" \
  --label "com.deepseek.source-manifest=$launch_manifest" \
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
  --env "VLLM_SPARSE_DENSE_QUERY_BLOCK=${VLLM_SPARSE_DENSE_QUERY_BLOCK:-8}" \
  --env HF_HOME=/runtime-cache/huggingface \
  --env TORCH_HOME=/runtime-cache/torch \
  --env TRITON_CACHE_DIR=/runtime-cache/triton \
  --env XDG_CACHE_HOME=/runtime-cache/xdg \
  --env TMPDIR=/runtime-tmp \
  --env NCCL_DEBUG=WARN \
  "$launch_image" "${vllm_args[@]}")
printf '%s\n' "$container_id" >"$RUN_DIR/${launch_mode}.container-id"

deadline=$((SECONDS + STARTUP_TIMEOUT_SECONDS))
while ! http_ready; do
  if ! container_running "$launch_container"; then
    docker_cmd logs --timestamps "$launch_container" \
      >"$LOG_DIR/startup-failed.log" 2>&1 || true
    die "container exited during startup; see $LOG_DIR/startup-failed.log"
  fi
  ((SECONDS < deadline)) || {
    docker_cmd logs --timestamps "$launch_container" \
      >"$LOG_DIR/startup-timeout.log" 2>&1 || true
    die "API startup timed out; container was left running for diagnosis"
  }
  log "waiting for $(api_url /v1/models)"
  sleep "$HEALTH_POLL_SECONDS"
done

{
  printf 'container=%s\n' "$launch_container"
  docker_cmd inspect --format 'id={{.Id}}' "$launch_container"
  docker_cmd inspect --format 'image={{.Image}}' "$launch_container"
  docker_cmd inspect --format 'created={{.Created}}' "$launch_container"
  docker_cmd inspect --format 'started={{.State.StartedAt}}' "$launch_container"
  docker_cmd inspect --format 'status={{.State.Status}}' "$launch_container"
  docker_cmd inspect --format 'network={{.HostConfig.NetworkMode}}' "$launch_container"
  docker_cmd inspect --format \
    'ports={{json .HostConfig.PortBindings}}' "$launch_container"
  docker_cmd inspect --format \
    'release={{index .Config.Labels "com.deepseek.release"}}' "$launch_container"
  docker_cmd inspect --format \
    'scheme={{index .Config.Labels "com.deepseek.scheme"}}' "$launch_container"
  docker_cmd inspect --format \
    'mode={{index .Config.Labels "com.deepseek.launch-mode"}}' "$launch_container"
} >"$RESULT_DIR/${launch_mode}-container-safe-inspect.txt"
printf 'SERVICE_START=PASS\nmode=%s\ncontainer=%s\ncontainer_id=%s\nurl=%s\n' \
  "$launch_mode" "$launch_container" "$container_id" "$(api_url /v1)"
printf 'scheme=%s\ngpus=%s\ntensor_parallel_size=%s\nmax_num_seqs=%s\n' \
  "$SCHEME_ID" "$GPU_DEVICES" "$TENSOR_PARALLEL_SIZE" "$MAX_NUM_SEQS"
