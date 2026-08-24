#!/usr/bin/env bash
set -euo pipefail

# shellcheck disable=SC1091
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

mode=r1
check_port=1
while (($#)); do
  case "$1" in
    --base) mode=base ;;
    --skip-port) check_port=0 ;;
    *) die "unknown preflight option: $1" ;;
  esac
  shift
done

ensure_runtime_dirs
init_docker
require_command sort
[[ "$NETWORK_MODE" == bridge ]] || die "only NETWORK_MODE=bridge is supported"
[[ "$HOST" == 0.0.0.0 ]] || die "container bind host must remain 0.0.0.0"
[[ "$API_PROBE_HOST" == 127.0.0.1 ]] || die \
  "host API probe must remain on loopback"
[[ "$HOST_PUBLISH_ADDRESS" == 127.0.0.1 ]] || die \
  "host API publication must remain on loopback"
[[ "$PORT" == 8005 ]] || die "API port must remain 8005"
[[ "$MODEL_DIR" == /* ]] || die "MODEL_DIR must be absolute: $MODEL_DIR"
[[ "$GPU_DEVICES" == "$PROFILE_GPU_DEVICES" ]] || die \
  "GPU_DEVICES must remain $PROFILE_GPU_DEVICES for scheme $SCHEME_ID"
[[ "$GPU_COUNT" == "$PROFILE_GPU_COUNT" ]] || die \
  "GPU_COUNT must remain $PROFILE_GPU_COUNT for scheme $SCHEME_ID"
[[ "$TENSOR_PARALLEL_SIZE" == "$PROFILE_TENSOR_PARALLEL_SIZE" ]] || die \
  "tensor parallel size must remain $PROFILE_TENSOR_PARALLEL_SIZE for scheme $SCHEME_ID"
[[ "$MAX_MODEL_LEN" == 262144 ]] || die "server context must remain 262144"
[[ "$MAX_NUM_SEQS" == "$PROFILE_MAX_NUM_SEQS" ]] || die \
  "scheduler ceiling must remain $PROFILE_MAX_NUM_SEQS for scheme $SCHEME_ID"
[[ "$BENCHMARK_MAX_CONCURRENCY" == \
  "$PROFILE_BENCHMARK_MAX_CONCURRENCY" ]] || die \
  "benchmark concurrency must remain $PROFILE_BENCHMARK_MAX_CONCURRENCY for scheme $SCHEME_ID"
IFS=',' read -r -a requested_gpus <<<"$GPU_DEVICES"
[[ "${#requested_gpus[@]}" == "$GPU_COUNT" ]] || die \
  "GPU_DEVICES must select exactly $GPU_COUNT devices: $GPU_DEVICES"
declare -A seen_gpus=()
for requested_gpu in "${requested_gpus[@]}"; do
  [[ -n "$requested_gpu" ]] || die "GPU_DEVICES contains an empty device"
  [[ -z "${seen_gpus[$requested_gpu]+present}" ]] || die \
    "GPU_DEVICES contains a duplicate device: $requested_gpu"
  seen_gpus[$requested_gpu]=1
done

image=$R1_IMAGE
manifest=r1-python.sha256
if [[ "$mode" == base ]]; then
  image=$BASE_IMAGE
  manifest=base-python.sha256
fi
docker_cmd image inspect "$image" >/dev/null 2>&1 || die \
  "required image is missing: $image"
verify_image_tree "$image" "$manifest"

mapfile -t gpu_inventory < <(
  docker_cmd run --rm --network none --gpus all \
    --entrypoint nvidia-smi "$image" \
    --query-gpu=name,memory.total,driver_version --format=csv,noheader,nounits
)
[[ "${#gpu_inventory[@]}" == 8 ]] || die \
  "exactly 8 visible GPUs are required; observed ${#gpu_inventory[@]}"
for gpu_entry in "${gpu_inventory[@]}"; do
  IFS=',' read -r gpu_name gpu_memory gpu_driver gpu_extra <<<"$gpu_entry"
  [[ -z "${gpu_extra:-}" ]] || die \
    "could not parse GPU inventory from nvidia-smi: $gpu_entry"
  gpu_name=${gpu_name#"${gpu_name%%[![:space:]]*}"}
  gpu_name=${gpu_name%"${gpu_name##*[![:space:]]}"}
  gpu_memory=${gpu_memory//[[:space:]]/}
  gpu_driver=${gpu_driver//[[:space:]]/}
  [[ "$gpu_name" == *A100* ]] || die "non-A100 GPU detected: $gpu_name"
  [[ "$gpu_memory" =~ ^[0-9]+$ ]] || die \
    "could not parse A100 memory from nvidia-smi: $gpu_entry"
  ((gpu_memory >= 80000)) || die \
    "A100-SXM4-80GB is required; observed ${gpu_memory} MiB: $gpu_name"
  [[ "$gpu_driver" =~ ^[0-9]+([.][0-9]+)+$ ]] || die \
    "could not parse NVIDIA driver version from nvidia-smi: $gpu_entry"
  mapfile -t ordered_drivers < <(
    printf '%s\n%s\n' "$MIN_NVIDIA_DRIVER" "$gpu_driver" | sort -V
  )
  [[ "${ordered_drivers[0]}" == "$MIN_NVIDIA_DRIVER" ]] || die \
    "NVIDIA driver $gpu_driver is below required $MIN_NVIDIA_DRIVER"
done

docker_cmd run --rm --network none \
  --mount "type=bind,src=$MODEL_DIR,dst=$CONTAINER_MODEL_DIR,readonly" \
  --volume "$R1_DIR:/audit:ro" \
  --volume "$CACHE_DIR:/runtime-cache:rw" \
  --volume "$TMP_DIR:/runtime-tmp:rw" \
  --env HF_HUB_OFFLINE=1 \
  --env TRANSFORMERS_OFFLINE=1 \
  --env XDG_CACHE_HOME=/runtime-cache \
  --env TMPDIR=/runtime-tmp \
  --entrypoint python3 "$image" \
  /audit/scripts/verify_model.py "$CONTAINER_MODEL_DIR" \
  >"$RESULT_DIR/model-verification.json"
tokenizer_check_args=()
if [[ "$mode" == base ]]; then
  # The immutable rollback image deliberately retains the reviewed 129283 bug.
  # Record that exact locked signature instead of applying the R1 invariant.
  tokenizer_check_args+=(--fixed-base-rollback)
fi
docker_cmd run --rm --network none \
  --mount "type=bind,src=$MODEL_DIR,dst=$CONTAINER_MODEL_DIR,readonly" \
  --volume "$R1_DIR:/audit:ro" \
  --volume "$CACHE_DIR:/runtime-cache:rw" \
  --volume "$TMP_DIR:/runtime-tmp:rw" \
  --env HF_HUB_OFFLINE=1 \
  --env TRANSFORMERS_OFFLINE=1 \
  --env XDG_CACHE_HOME=/runtime-cache \
  --env TMPDIR=/runtime-tmp \
  --entrypoint python3 "$image" \
  /audit/tests/tokenizer_runtime_check.py \
  "${tokenizer_check_args[@]}" "$CONTAINER_MODEL_DIR" \
  >"$RESULT_DIR/tokenizer-runtime.json"

if ((check_port)); then
  if container_exists "$CONTAINER_NAME" && container_running "$CONTAINER_NAME"; then
    die "release container is already running: $CONTAINER_NAME"
  fi
  if command -v ss >/dev/null 2>&1 && ss -H -ltn "sport = :$PORT" | grep -q .; then
    die "TCP port is already listening: $PORT"
  fi
fi

printf 'PREFLIGHT=PASS\nmode=%s\nscheme=%s\nimage=%s\nvisible_gpus=%s\nselected_gpus=%s\ndriver=%s\n' \
  "$mode" "$SCHEME_ID" "$image" "${#gpu_inventory[@]}" "$GPU_DEVICES" \
  "$gpu_driver"
