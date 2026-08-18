#!/usr/bin/env bash
set -euo pipefail

required_commit=f8ea5bb163c161ef38b401d055cc5fd4a934091a
jobs=${TARGET_BUILD_JOBS:-16}
nvcc_threads=${NVCC_THREADS:-1}
output_dir=/offline-output
work_dir=/opt/dsv4-build-work

[[ "$jobs" =~ ^[1-9][0-9]*$ ]] || { echo "invalid TARGET_BUILD_JOBS=$jobs" >&2; exit 2; }
[[ "$nvcc_threads" =~ ^[1-9][0-9]*$ ]] || { echo "invalid NVCC_THREADS=$nvcc_threads" >&2; exit 2; }
[[ "$(git -C /opt/dsv4-source rev-parse HEAD)" == "$required_commit" ]] || {
  echo "fixed vLLM source commit mismatch" >&2; exit 1;
}
mkdir -p "$output_dir/csrc" "$output_dir/final" "$output_dir/manifests"
rm -rf "$work_dir"
mkdir -p "$work_dir"
cp -a /opt/dsv4-source "$work_dir/csrc-source"
cp -a /opt/dsv4-source "$work_dir/final-source"

export MAX_JOBS="$jobs"
export NVCC_THREADS="$nvcc_threads"
export TORCH_CUDA_ARCH_LIST=8.0
export VLLM_TARGET_DEVICE=cuda
export VLLM_DOCKER_BUILD_CONTEXT=1
export CMAKE_BUILD_TYPE=Release
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HF_DATASETS_OFFLINE=1
export VLLM_NO_USAGE_STATS=1 DO_NOT_TRACK=1
export VLLM_CUTLASS_SRC_DIR=/opt/vendor/cutlass
export TRITON_KERNELS_SRC_DIR=/opt/vendor/triton/python/triton_kernels/triton_kernels
export DEEPGEMM_SRC_DIR=/opt/vendor/deepgemm
export FMHA_SM100_SRC_DIR=/opt/vendor/msa
export FLASH_MLA_SRC_DIR=/opt/vendor/flashmla
export FLASH_KDA_SRC_DIR=/opt/vendor/flashkda
export QUTLASS_SRC_DIR=/opt/vendor/qutlass
export TML_FA4_SRC_DIR=/opt/vendor/tml-fa4
export VLLM_FLASH_ATTN_SRC_DIR=/opt/vendor/vllm-flash-attention

echo "[1/3] compiling A100 (SM80) CUDA extensions; jobs=$jobs nvcc_threads=$nvcc_threads"
cd "$work_dir/csrc-source"
rm -rf .deps build dist
# The full source tree contains optional Rust setuptools targets. Supplying the
# already verified artifacts makes setup.py skip Cargo; otherwise an offline
# target without the Rust toolchain would waste time and emit an optional-build
# failure before/after the CUDA build.
cp -a /opt/offline-artifacts/vllm-rs vllm/vllm-rs
find /opt/offline-artifacts -maxdepth 1 -type f -name '_rust_*.so' -exec cp -a {} vllm/ \;
export SETUPTOOLS_SCM_PRETEND_VERSION=0.0.0+csrc.build
python3 setup.py bdist_wheel --dist-dir "$output_dir/csrc" --py-limited-api=cp38
unset SETUPTOOLS_SCM_PRETEND_VERSION
csrc_wheel=$(find "$output_dir/csrc" -maxdepth 1 -type f -name '*.whl' -print -quit)
[[ -n "$csrc_wheel" ]] || { echo "csrc wheel was not generated" >&2; exit 1; }

echo "[2/3] assembling the exact-commit vLLM wheel"
cd "$work_dir/final-source"
rm -rf .deps build dist
cp -a /opt/offline-artifacts/vllm-rs vllm/vllm-rs
find /opt/offline-artifacts -maxdepth 1 -type f -name '_rust_*.so' -exec cp -a {} vllm/ \;
export VLLM_USE_PRECOMPILED=1
export VLLM_PRECOMPILED_WHEEL_LOCATION="$csrc_wheel"
export VLLM_SKIP_PRECOMPILED_VERSION_SUFFIX=1
python3 setup.py bdist_wheel --dist-dir "$output_dir/final" --py-limited-api=cp38
final_wheel=$(find "$output_dir/final" -maxdepth 1 -type f -name '*.whl' -print -quit)
[[ -n "$final_wheel" ]] || { echo "final vLLM wheel was not generated" >&2; exit 1; }

echo "[3/3] installing and validating without network"
python3 -m pip install --no-index --no-deps --force-reinstall "$final_wheel"
python3 - <<'PY'
import importlib.metadata as metadata
import torch
import vllm
from vllm.config.speculative import SpeculativeConfig
assert torch.__version__.startswith("2.13.0"), torch.__version__
assert torch.version.cuda == "13.0", torch.version.cuda
text = open("/opt/dsv4-source/vllm/config/speculative.py", encoding="utf-8").read()
assert "dspark" in text
print("vllm=" + vllm.__version__)
print("torch=" + torch.__version__)
print("torch_cuda=" + str(torch.version.cuda))
print("triton=" + metadata.version("triton"))
print("SpeculativeConfig=" + SpeculativeConfig.__name__)
PY
# Building may happen on a CPU-only online workstation.  This pinned commit
# probes the current platform even for CLI help, so construct the parser with a
# minimal synthetic A100 platform and defer real CUDA/NVML verification to the
# target host's verify_image.sh.
python3 - <<'PY' >"$output_dir/manifests/vllm-serve-help.txt"
import sys

import vllm.platforms as platforms
from vllm.platforms.interface import DeviceCapability, Platform, PlatformEnum

OfflineA100Platform = type(
    "OfflineA100Platform",
    (Platform,),
    {
        "_enum": PlatformEnum.CUDA,
        "device_type": "cuda",
        "device_name": "NVIDIA A100-SXM4-80GB",
        "dist_backend": "nccl",
        "get_device_capability": classmethod(
            lambda cls, *args, **kwargs: DeviceCapability(8, 0)
        ),
        "device_count": classmethod(lambda cls: 8),
    },
)
platforms._current_platform = OfflineA100Platform()

from vllm.entrypoints.cli.main import main

sys.argv = ["vllm", "serve", "--help=all"]
main()
PY
for required_flag in \
  --tensor-parallel-size --trust-remote-code --kv-cache-dtype --host --port \
  --max-model-len --max-num-seqs --block-size --tokenizer-mode \
  --tool-call-parser --reasoning-parser --enable-auto-tool-choice \
  --served-model-name --enforce-eager --speculative-config; do
  grep -F -- "$required_flag" "$output_dir/manifests/vllm-serve-help.txt" \
    >/dev/null || {
      echo "required CLI flag missing: $required_flag" >&2
      exit 1
    }
done
python3 -m pip freeze --all >"$output_dir/manifests/pip-freeze.txt"
(
  cd "$output_dir"
  sha256sum csrc/*.whl final/*.whl >manifests/wheels.sha256
)
printf 'commit=%s\ntorch_cuda_arch_list=8.0\nmax_jobs=%s\nnvcc_threads=%s\nnetwork=none\n' \
  "$required_commit" "$jobs" "$nvcc_threads" \
  >"$output_dir/manifests/target-build-info.txt"
# Do not bake temporary object files or duplicated source trees into the
# committed runtime image. Wheels and manifests remain on the bind mount.
cd /
rm -rf "$work_dir"
echo "TARGET_OFFLINE_BUILD=PASS wheel=$final_wheel"
