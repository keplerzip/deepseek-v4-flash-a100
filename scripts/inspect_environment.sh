#!/usr/bin/env bash
set -uo pipefail

ROOT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
REPORT_DIR="$ROOT_DIR/reports"
REPORT_FILE="$REPORT_DIR/environment-report.txt"
COMPAT_FILE="$REPORT_DIR/compatibility-report.md"
MIN_DRIVER=580.126.20
mkdir -p "$REPORT_DIR"

section() {
  printf '\n===== %s =====\n' "$1"
}

run_optional() {
  local title=$1
  shift
  section "$title"
  printf '$'
  printf ' %q' "$@"
  printf '\n'
  "$@" 2>&1
  local rc=$?
  if ((rc != 0)); then
    printf '[exit=%d; command unavailable or not permitted]\n' "$rc"
  fi
  return 0
}

docker_read() {
  if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
    docker "$@"
  elif command -v sudo >/dev/null 2>&1 && sudo -n docker info >/dev/null 2>&1; then
    sudo -n docker "$@"
  else
    return 127
  fi
}

{
  section "audit metadata"
  printf 'timestamp_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  printf 'hostname=%s\n' "$(hostname 2>/dev/null || true)"
  printf 'bundle_root=%s\n' "$ROOT_DIR"
  printf 'expected_target=Ubuntu 22.04 x86_64, 8 x A100-SXM4-80GB\n'
  printf 'required_vllm_commit=f8ea5bb163c161ef38b401d055cc5fd4a934091a\n'
  printf 'required_cuda_runtime=13.0.3\n'
  printf 'minimum_linux_driver=%s\n' "$MIN_DRIVER"

  run_optional "os release" cat /etc/os-release
  run_optional "kernel" uname -a
  run_optional "glibc" ldd --version
  run_optional "gcc" gcc --version
  run_optional "cmake" cmake --version
  run_optional "default python" python3 --version

  section "python 3.10 through 3.14"
  for minor in 10 11 12 13 14; do
    if command -v "python3.$minor" >/dev/null 2>&1; then
      "python3.$minor" --version 2>&1
    else
      printf 'python3.%s: not found\n' "$minor"
    fi
  done

  run_optional "nvidia-smi" nvidia-smi
  run_optional "nvidia-smi query" nvidia-smi -q
  run_optional "gpu topology" nvidia-smi topo -m
  run_optional "gpu inventory" nvidia-smi --query-gpu=index,name,memory.total,driver_version,compute_cap,uuid --format=csv,noheader
  run_optional "gpu compute processes" nvidia-smi --query-compute-apps=gpu_uuid,pid,process_name,used_gpu_memory --format=csv,noheader
  run_optional "nvcc" nvcc --version
  run_optional "docker client" docker --version
  run_optional "podman client" podman --version
  run_optional "nvidia container toolkit" nvidia-container-cli --version

  section "docker server (read only)"
  docker_read version 2>&1 || printf '[docker server unavailable; direct and sudo -n both failed]\n'
  section "existing containers (read only)"
  docker_read ps --no-trunc --format '{{.ID}}\t{{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}' 2>&1 || true

  run_optional "filesystems" df -h
  run_optional "memory" free -h
  run_optional "shared memory" df -h /dev/shm
  run_optional "numa" numactl --hardware
  run_optional "listening port 8005" sh -c "ss -ltnp 2>/dev/null | awk 'NR == 1 || /:8005/'"
  run_optional "relevant host processes" sh -c "ps -eo user,pid,ppid,etimes,cmd --sort=pid | grep -Ei 'MiniMax|vllm|DeepSeek|ray::|python' | grep -v grep || true"

  section "build prerequisites"
  for tool in git docker podman gcc g++ cmake ninja make python3 nvcc nvidia-smi zstd sha256sum; do
    if command -v "$tool" >/dev/null 2>&1; then
      printf '%-24s %s\n' "$tool" "$(command -v "$tool")"
    else
      printf '%-24s MISSING\n' "$tool"
    fi
  done
} >"$REPORT_FILE" 2>&1

driver_version=""
gpu_count=0
gpu_names=""
if command -v nvidia-smi >/dev/null 2>&1; then
  driver_version=$(nvidia-smi --query-gpu=driver_version --format=csv,noheader 2>/dev/null | head -n1 | tr -d ' ')
  gpu_count=$(nvidia-smi --query-gpu=index --format=csv,noheader 2>/dev/null | wc -l)
  gpu_names=$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | sort -u | paste -sd ', ' -)
fi

driver_state=UNKNOWN
if [[ -n "$driver_version" ]]; then
  if [[ "$(printf '%s\n%s\n' "$MIN_DRIVER" "$driver_version" | sort -V | head -n1)" == "$MIN_DRIVER" ]]; then
    driver_state=PASS
  else
    driver_state=FAIL
  fi
fi

docker_state=FAIL
if docker_read info >/dev/null 2>&1; then
  docker_state=PASS
fi

gpu_state=FAIL
if [[ "$gpu_count" == 8 ]] && [[ "$gpu_names" == *A100* ]]; then
  gpu_state=PASS
fi

model_dir="${MODEL_DIR:-}"
if [[ -z "$model_dir" && -r "$ROOT_DIR/config/model.env" ]]; then
  # shellcheck disable=SC1091
  source "$ROOT_DIR/config/model.env"
  model_dir=${MODEL_DIR:-}
fi
model_state=FAIL
if [[ -n "$model_dir" && -f "$model_dir/config.json" && -f "$model_dir/model.safetensors.index.json" ]]; then
  model_state=PASS
fi

cat >"$COMPAT_FILE" <<EOF
# Target compatibility report

Generated: $(date -u +%Y-%m-%dT%H:%M:%SZ)

| Check | Result | Observed / required |
|---|---:|---|
| Architecture | $( [[ $(uname -m) == x86_64 ]] && printf PASS || printf FAIL ) | observed: $(uname -m); required: x86_64 |
| NVIDIA driver | $driver_state | observed: ${driver_version:-not detected}; CUDA 13.0.3 minimum: $MIN_DRIVER |
| GPU inventory | $gpu_state | observed: $gpu_count GPU(s), ${gpu_names:-not detected}; required: 8 x A100 |
| Docker server access | $docker_state | supports direct Docker or \`sudo -n docker\` |
| Local model directory | $model_state | ${model_dir:-not configured} |

The source baseline is exactly \`haosdent/vllm@f8ea5bb163c161ef38b401d055cc5fd4a934091a\`.
Its Dockerfile pins Ubuntu 22.04, CUDA 13.0.3, Python 3.12 and NCCL 2.30.7;
its Python metadata pins Torch 2.13.0. No driver or host package is changed by
this audit. A PASS here is an environment prerequisite, not proof of model
inference correctness.
EOF

printf 'Wrote %s\nWrote %s\n' "$REPORT_FILE" "$COMPAT_FILE"
if [[ "$driver_state" == FAIL || "$docker_state" == FAIL || "$gpu_state" == FAIL ]]; then
  exit 2
fi
