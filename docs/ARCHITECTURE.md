# 版本、组件和安全边界

## 固定身份

| 组件 | 固定值 |
|---|---|
| vLLM repository | `https://github.com/haosdent/vllm.git` |
| source commit | `f8ea5bb163c161ef38b401d055cc5fd4a934091a` |
| vLLM version | `0.1.dev1+gf8ea5bb16` |
| Python | 3.12 |
| Torch | `2.13.0+cu130` |
| CUDA runtime | 13.0 |
| Triton | 3.7.1 |
| NCCL | base env 2.28.3-1；Python wheel 2.29.7；实际加载值由目标机验证 |
| target architecture | x86_64 / SM80 |
| runtime image | `dsv4-a100-vllm:f8ea5bb` |

源码不是 stock vLLM，也不是 fork 的最新 HEAD。任何替换 commit、Torch/CUDA 组合或
现有 CUDA 12.9 nightly 镜像都需要重新构建和完整验收。

## 目标硬件

- Ubuntu 22.04.4 LTS；
- 8×A100-SXM4-80GB；
- NV12 全互联 / NVLink-NVSwitch；
- Driver 580.159.04；
- Docker 29.5.1；
- 约 2TiB 系统内存；
- 模型位于 `/ai` 阵列。

CUDA 13.0 Update 3 的 Linux 最低驱动要求在本项目中按 580.126.20 检查；目标 Driver
高于该值。脚本只检查，不升级 Driver。

NCCL 存在分层元数据：`image-inspect.json` 的基础 CUDA 环境是 2.28.3-1，最终
`pip-freeze.txt` 是 `nvidia-nccl-cu13==2.29.7`。源码 Dockerfile 构建日志还出现过
2.30.7 override 意图，但它不是最终 pip freeze。`verify_image.sh` 现在会在目标 GPU
环境记录 `torch.cuda.nccl.version()`，该值才用于确认 PyTorch 实际加载版本。

## 容器结构

```text
Codex / New API
       |
       v
172.17.0.1:8005
       |
dsv4-target-only-f8ea5bb  或  dsv4-dspark-f8ea5bb
       |
       +-- read-only: /ai/models/... -> /models/deepseek-v4-flash-0731
       +-- writable:  run/cache/<mode>
       +-- writable:  run/tmp/<mode>
       +-- GPU 0..7, TP=8
```

两个容器名和缓存目录独立，但共用 GPU、端口和 `run/dsv4-a100.lock`，所以互斥。

## Docker 29 GPU 参数

Docker 29 在本机对 `--gpus "device=0,1,..."` 返回：

```text
cannot set both Count and DeviceIDs on device request
```

启动器已改用 `--gpus all`，同时通过 `CUDA_VISIBLE_DEVICES=0,1,2,3,4,5,6,7` 固定
容器内顺序，vLLM 参数固定 TP=8。Podman 分支使用 `--device nvidia.com/gpu=all`。

## 模型结构

现场验证：

- 48 个 safetensors shard；
- 72,317 个 index 权重 key；
- 72,317 个 header tensor key；
- `architectures=['DeepseekV4ForCausalLM']`；
- `model_type='deepseek_v4'`；
- 166,898,658,872 bytes / 155.44 GiB；
- `mtp.0.*` 权重存在；
- `encoding/` 与 `inference/` 来自单独保存的官方辅助文件。

`verify_model.py` 只读取 JSON 与 safetensors metadata，不加载张量。

## 两种执行路径

target-only：

```text
DeepSeek V4 target -> token
```

DSpark：

```text
同一 checkpoint 内 DSpark/mtp 权重提出最多 5 个 token
             -> target 验证 -> 接受前缀 / 回退
```

DSpark 只提升 decode，不能提高 prefill。它还会占用 draft workspace、CUDA Graph 和临时
张量显存，因此相同 `gpu_memory_utilization` 下比 target-only 更容易在长上下文触发 OOM。

## 离线边界

镜像不包含模型。运行时设置：

```text
HF_HUB_OFFLINE=1
TRANSFORMERS_OFFLINE=1
HF_DATASETS_OFFLINE=1
VLLM_NO_USAGE_STATS=1
```

真正断网测试使用容器 `--network none`。固定源码、构建清单、pip freeze、镜像 inspect、
许可证与 SHA256 均在包中保留。

## 不做的事情

- 不升级 NVIDIA Driver；
- 不下载或复制 167GB 权重；
- 不自动停止 MiniMax；
- 不删除其他容器或 Python 环境；
- 不强制 Hopper/Blackwell kernel；
- 不把社区参考性能写成本机实测；
- 不把 `FILE_INTEGRITY=PASS` 写成推理正确性通过。
