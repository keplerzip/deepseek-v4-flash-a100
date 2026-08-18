# Target compatibility report

> Historical build-host audit. It was generated before A100 field validation.
> Current target results are recorded in `performance-report-2026-08-13.md`.
> NCCL metadata was later reconciled: base image env 2.28.3-1, final Python
> wheel 2.29.7, while 2.30.7 below is the source Dockerfile override intent,
> not the final frozen Python package.

Generated: 2026-08-13T07:25:55Z

| Check | Result | Observed / required |
|---|---:|---|
| Architecture | PASS | observed: x86_64; required: x86_64 |
| NVIDIA driver | UNKNOWN | observed: not detected; CUDA 13.0.3 minimum: 580.126.20 |
| GPU inventory | FAIL | observed: 0 GPU(s), not detected; required: 8 x A100 |
| Docker server access | PASS | supports direct Docker or `sudo -n docker` |
| Local model directory | FAIL | /ai/models/deepseek-v4-flash-0731-modelscope |

The source baseline is exactly `haosdent/vllm@f8ea5bb163c161ef38b401d055cc5fd4a934091a`.
Its Dockerfile pins Ubuntu 22.04, CUDA 13.0.3, Python 3.12 and NCCL 2.30.7;
its Python metadata pins Torch 2.13.0. No driver or host package is changed by
this audit. A PASS here is an environment prerequisite, not proof of model
inference correctness.

## User-provided target inventory (not remotely executed by this build host)

The target report supplied on 2026-08-13 states Ubuntu 22.04.4 x86_64,
8 x NVIDIA A100-SXM4-80GB, Driver 580.159.04, Docker 29.5.1, 48 checkpoint
shards and about 156 GiB at the configured model path. It also shows the
MiniMax container is currently running. Target scripts independently re-check
these facts; inference startup is intentionally blocked while MiniMax owns GPUs.

## Network-isolated seed configure

`reports/offline-cmake-configure.log` records a successful CMake configure in
the final seed image with Docker `--network none`, CUDA architecture 8.0 and
NVCC 13.0.88. It resolved every FetchContent input from `/opt/vendor`, enabled
SM80 fallback kernels, and correctly skipped DeepGEMM, FlashMLA, FlashKDA and
Qutlass GPU kernels that require newer architectures. This proves offline
configuration completeness, but it is not a CUDA compilation or inference pass.
