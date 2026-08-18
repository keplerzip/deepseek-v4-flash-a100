#!/usr/bin/env bash
set -euo pipefail

# shellcheck disable=SC1091
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib.sh"
load_mode_config target-only
detect_runtime

image_present || die "image is not imported: $IMAGE_NAME; run scripts/install_offline.sh"
revision=$(runtime image inspect --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$IMAGE_NAME")
[[ "$revision" == "$VLLM_COMMIT" ]] || \
  die "image revision mismatch: observed ${revision:-empty}, required $VLLM_COMMIT"

help_file="$REPORT_DIR/vllm-serve-help.txt"
version_file="$REPORT_DIR/runtime-versions.txt"
gpu_args=()
case "$RUNTIME_KIND" in
  docker) gpu_args=(--gpus all) ;;
  podman) gpu_args=(--device nvidia.com/gpu=all) ;;
esac
runtime run --rm --network none "${gpu_args[@]}" \
  --entrypoint vllm "$IMAGE_NAME" serve --help=all >"$help_file"

required_flags=(
  --tensor-parallel-size
  --trust-remote-code
  --kv-cache-dtype
  --host
  --port
  --max-model-len
  --max-num-seqs
  --block-size
  --tokenizer-mode
  --tool-call-parser
  --reasoning-parser
  --enable-auto-tool-choice
  --served-model-name
  --enforce-eager
  --speculative-config
  --max-num-batched-tokens
  --kv-cache-memory-bytes
)
for flag in "${required_flags[@]}"; do
  grep -F -- "$flag" "$help_file" >/dev/null || die "required CLI flag missing: $flag"
done

runtime run --rm --network none "${gpu_args[@]}" \
  --entrypoint python3 "$IMAGE_NAME" -c '
import importlib.metadata as m
import torch, vllm
print("python=" + __import__("sys").version.replace("\n", " "))
print("vllm=" + vllm.__version__)
print("torch=" + torch.__version__)
print("torch_cuda=" + str(torch.version.cuda))
try:
    print("torch_cuda_nccl=" + str(torch.cuda.nccl.version()))
except Exception as exc:
    print("torch_cuda_nccl=UNAVAILABLE:" + type(exc).__name__ + ":" + str(exc))
print("visible_gpus=" + str(torch.cuda.device_count()))
assert torch.cuda.device_count() == 8, torch.cuda.device_count()
for name in ("triton", "nvidia-nccl-cu13", "nvidia-nccl-cu12"):
    try:
        print(name + "=" + m.version(name))
    except m.PackageNotFoundError:
        pass
from vllm.config.speculative import SpeculativeConfig
source = __import__("inspect").getsource(SpeculativeConfig)
print("dspark_source_support=" + str("dspark" in source))
' >"$version_file"
grep -F 'dspark_source_support=True' "$version_file" >/dev/null || \
  die "image does not expose DSpark support"

printf 'IMAGE_VERIFICATION=PASS\n'
printf 'revision=%s\n' "$revision"
printf 'cli_help=%s\nversions=%s\n' "$help_file" "$version_file"
