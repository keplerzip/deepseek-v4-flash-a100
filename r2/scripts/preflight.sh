#!/usr/bin/env bash
set -euo pipefail

# shellcheck disable=SC1091
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

skip_port=0
while (($#)); do
  case "$1" in
    --skip-port) skip_port=1 ;;
    *) die "unknown preflight option: $1" ;;
  esac
  shift
done

ensure_runtime_dirs
init_docker
require_command sort
[[ "$NETWORK_MODE" == bridge ]] || die 'only the Docker bridge network is supported'
[[ "$HOST" == 0.0.0.0 ]] || die 'container bind address must remain 0.0.0.0'
[[ "$HOST_PUBLISH_ADDRESS" == 127.0.0.1 ]] || die \
  'host publication must remain loopback-only'
[[ "$PORT" == 8005 ]] || die 'API port must remain 8005'
[[ "$GPU_DEVICES" == 0,1,2,3,4,5,6,7 ]] || die 'all and only GPU 0-7 are required'
[[ "$GPU_COUNT" == 8 && "$TENSOR_PARALLEL_SIZE" == 8 ]] || die 'R2 requires TP8 on 8 GPUs'
[[ "$MAX_MODEL_LEN" == 1048576 ]] || die 'physical context must remain 1,048,576'
[[ "$MAX_NUM_SEQS" == 16 ]] || die 'max-num-seqs must remain 16'
[[ "$BLOCK_SIZE" == 256 ]] || die 'DeepSeek V4 block size must remain 256'
[[ "$KV_CACHE_DTYPE" == fp8 ]] || die 'KV cache dtype must remain fp8'
[[ "$MODEL_DIR" == /* ]] || die "MODEL_DIR must be absolute: $MODEL_DIR"

docker_cmd image inspect "$R2_IMAGE" >/dev/null 2>&1 || die \
  "required precompiled image is missing: $R2_IMAGE"
revision=$(docker_cmd image inspect --format \
  '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$R2_IMAGE")
[[ "$revision" == "$R2_SOURCE_COMMIT" ]] || die \
  "image revision mismatch: expected=$R2_SOURCE_COMMIT observed=$revision"
[[ "$(docker_cmd image inspect --format '{{index .Config.Labels "com.deepseek.build.max-jobs"}}' "$R2_IMAGE")" == 8 ]] || die \
  'image was not provenance-labeled as an 8-job build'
[[ "$(docker_cmd image inspect --format '{{index .Config.Labels "com.deepseek.build.nvcc-threads"}}' "$R2_IMAGE")" == 1 ]] || die \
  'image NVCC thread provenance must be 1'
[[ "$(docker_cmd image inspect --format '{{index .Config.Labels "com.deepseek.cuda.arch"}}' "$R2_IMAGE")" == 8.0 ]] || die \
  'image CUDA architecture provenance must be SM80/8.0'

mapfile -t gpu_inventory < <(
  docker_cmd run --rm --network none --gpus all --entrypoint nvidia-smi "$R2_IMAGE" \
    --query-gpu=name,memory.total,driver_version --format=csv,noheader,nounits
)
[[ "${#gpu_inventory[@]}" == 8 ]] || die \
  "exactly 8 visible GPUs are required; observed ${#gpu_inventory[@]}"
for gpu_entry in "${gpu_inventory[@]}"; do
  IFS=',' read -r gpu_name gpu_memory gpu_driver gpu_extra <<<"$gpu_entry"
  [[ -z "${gpu_extra:-}" ]] || die "could not parse GPU inventory: $gpu_entry"
  gpu_name=${gpu_name#"${gpu_name%%[![:space:]]*}"}
  gpu_name=${gpu_name%"${gpu_name##*[![:space:]]}"}
  gpu_memory=${gpu_memory//[[:space:]]/}
  gpu_driver=${gpu_driver//[[:space:]]/}
  [[ "$gpu_name" == *A100* ]] || die "non-A100 GPU detected: $gpu_name"
  [[ "$gpu_memory" =~ ^[0-9]+$ ]] && ((gpu_memory >= 80000)) || die \
    "A100 80GB is required; observed: $gpu_entry"
  mapfile -t ordered < <(printf '%s\n%s\n' "$MIN_NVIDIA_DRIVER" "$gpu_driver" | sort -V)
  [[ "${ordered[0]}" == "$MIN_NVIDIA_DRIVER" ]] || die \
    "NVIDIA driver $gpu_driver is below $MIN_NVIDIA_DRIVER"
done

# Import both the model architecture and the actual worker entry point before
# accepting either deployment path. The Worker import traverses warmup and the
# MRV2 model runner, which catches cross-module backport mismatches before the
# long-lived API container starts.
docker_cmd run --rm --network none --gpus all \
  --entrypoint python3 "$R2_IMAGE" -c '
from vllm.models.deepseek_v4 import DeepseekV4ForCausalLM
from vllm.v1.worker.gpu_worker import Worker
assert DeepseekV4ForCausalLM.__name__ == "DeepseekV4ForCausalLM"
assert Worker.__name__ == "Worker"
' || die 'DeepSeek V4 model/Worker import failed inside the runtime image'

docker_cmd run --rm --network none \
  --mount "type=bind,src=$MODEL_DIR,dst=$CONTAINER_MODEL_DIR,readonly" \
  --volume "$R2_DIR:/audit:ro" \
  --entrypoint python3 "$R2_IMAGE" \
  /audit/scripts/verify_model.py "$CONTAINER_MODEL_DIR" \
  >"$RESULT_DIR/model-verification.json"

docker_cmd run --rm --network none \
  --env "VLLM_SERVED_MODEL_MAX_LENS=$SERVED_MODEL_MAX_LENS" \
  --entrypoint python3 "$R2_IMAGE" -c '
from vllm.entrypoints.serve.utils.model_limits import get_served_model_max_len
expected={"deepseek-v4-flash":262144,"deepseek-v4-flash[1M]":1048576,"deepseek-v4-flash-claude":262144,"deepseek-v4-flash-claude[1M]":1048576}
observed={name:get_served_model_max_len(name,1048576) for name in expected}
assert observed == expected, (observed, expected)
' || die 'per-alias context-limit contract failed inside the runtime image'

if ((!skip_port)); then
  if command -v ss >/dev/null 2>&1 && ss -H -ltn "sport = :$PORT" | grep -q .; then
    die "TCP port is already listening: $PORT"
  fi
fi

printf 'PREFLIGHT=PASS\nscheme=%s\nimage=%s\nvisible_gpus=8\ngpus=%s\ntp=8\nmax_num_seqs=16\ncache_profile=%s\n' \
  "$SCHEME_ID" "$R2_IMAGE" "$GPU_DEVICES" "$PREFIX_CACHE_PROFILE"
