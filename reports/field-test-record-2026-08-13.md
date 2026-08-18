# 2026-08-13 目标机现场测试记录

本文按时间顺序保存操作者发回的关键终端证据。大段重复 shard 加载进度和重复 TileLang
告警已折叠；数值与文件名保持原样。原始文件仍应从目标机归档回来。

## 目标机与模型

```text
Ubuntu 22.04.4 LTS, x86_64
8 x NVIDIA A100-SXM4-80GB, 81920 MiB
Driver 580.159.04
Docker client/server 29.5.1
MODEL_DIR=/ai/models/deepseek-v4-flash-0731-modelscope
```

模型验证：

```text
architectures=['DeepseekV4ForCausalLM']
model_type=deepseek_v4
shards_metadata_checked=48
index_referenced_shards=48
index_weight_keys=72317
header_tensor_keys=72317
directory_files=74
directory_bytes=166898658872
directory_human=155.44 GiB
FILE_INTEGRITY=PASS
DSPARK_WEIGHTS=PASS
INFERENCE_CORRECTNESS=NOT_TESTED
```

ModelScope 首次下载缺少 `encoding/` 和 `inference/`，使用单独保存的官方辅助包补齐后
通过验证。

## 运行时兼容修复

GPU guard 首次将 `gpu-monitor-dcgm-exporter` 识别为 GPU 容器并阻止启动。人工确认
`nvidia-smi --query-compute-apps` 为空后曾用 `FORCE_START=1` 绕过。项目现改为精确
allowlist，不再需要全局 FORCE。

Docker 29 首次启动报错：

```text
cannot set both Count and DeviceIDs on device request
```

现场把 `gpu_args=(--gpus "device=$GPU_DEVICES")` 改为 `gpu_args=(--gpus all)` 后成功。
该修复已固化，同时保留 `CUDA_VISIBLE_DEVICES=0..7`。

## target-only smoke

```text
passed=13
failed=1
required_failed=0
```

13 项包括模型列表、中文、数学、Python、TCL/EDA、JSON、三个 reasoning effort、多轮、
长 prompt、stream 和 completions。唯一可选失败为 `reasoning_content field`。

复杂推理手工检查：

```text
message_keys=['annotations','audio','content','function_call','reasoning','refusal','role']
finish_reason=length
content_length=0
reasoning_content_present=False
usage.prompt_tokens=132
usage.completion_tokens=1024
```

## target-only 32K eager

结果：`benchmark-target-only-20260813T153055Z.json`

```text
prompt=1024 output=512 C1  ttft=0.1566580543  decode=7.8903724694
prompt=11000 output=512 C1 ttft=1.3379516546  decode=7.2892546749
```

该运行用于功能排障，不能代表 CUDA Graph 性能。

## DSpark 加载证据

```text
SpeculativeConfig(method='dspark', model=同一本地模型,
  num_spec_tokens=5, max_model_len=262144, tensor_parallel_size=8)
DSpark draft model loaded: 96 params
Capturing model for DSpark speculator... rank0..rank7
Capturing dspark CUDA graphs (FULL): 100%
```

启动日志还提示 speculative 调度把 `max_num_scheduled_tokens` 设为 2032，可能不是最优。

## DSpark 0.95 单路矩阵与 OOM

结果：`benchmark-dspark-20260813T155555Z.json`

```text
1K/512 C1:   1/1, TTFT 0.1579344971, decode 341.4170341539
11K/512 C1:  1/1, TTFT 1.3444004990, decode 301.3308396215
128K/512 C1: 1/1, TTFT 25.0283198068, decode 218.8443225500
260K/512 C1: 0/1
```

失败日志显示尝试分配 400,556,032 bytes 时每卡只剩约 7–9MiB，随后 API shutdown。

## DSpark acceptance 快照

```text
mean=5.00 accepted=24/30   rate=80.0%
mean=5.00 accepted=8/10    rate=80.0%
mean=3.31 accepted=296/640 rate=46.2%
mean=3.17 accepted=26/60   rate=43.3%
mean=2.28 accepted=409/1595 rate=25.6%
```

## DSpark 长上下文 Graph 与 eager

手工 16GiB KV 配置：

```text
260K/128 C2: 2/2, TTFT 60.4326277133, decode 105.7981588263
260K/128 C3: 1/3, TTFT 61.2693251194, decode 20.1941212762
```

C3 随后多 rank 报 `CUDA illegal memory access`、`c10::AcceleratorError`，模型进程退出。

相同量级改为 eager：

```text
260K/128 C3: 3/3, TTFT 119.7532878118, decode 27.3231851892
```

后续 0.80/C6 Graph 现场结果：

```text
260K/128 C6: 6/6, TTFT 180.4758001957, decode 48.8916748123
benchmark-dspark-20260813T174933Z.json
```

## target-only 0.92 / 300K / C12

结果：`benchmark-target-only-20260813T173354Z.json`

```text
1K/1024 C12: 12/12
TTFT P50=0.7791148955, P95=1.3024118561
prompt TPS proxy=1048.88261263
per-request decode TPS=36.5510008448
aggregate TPS=425.3376574729
E2E P50=28.8402900808
```

启动日志：

```text
Model loading took 19.81 GiB memory and 28.295916 seconds
Available KV cache memory: 49.83 GiB
GPU KV cache size: 5,674,802 tokens
Maximum concurrency for 307,200 tokens per request: 18.47x
```

GPU 显存峰值为 77,211–77,221MiB；GPU1–7 平均利用率约 97.3%。完整逐卡数据见
`reports/data/target-gpu-summary-2026-08-13.csv`。

单路 300K：

```text
300K/128 C1: 1/1, TTFT 73.1475624340, decode 30.7540095323
benchmark-target-only-20260813T173818Z.json
```

## 512K max_num_batched_tokens 调优

```text
2048: KV 14.21x
  C1  TTFT 9.1394595709  decode 56.0281822560
  C4  TTFT 18.9323970182 decode 16.8643485482
  C10 TTFT 50.4616595785 decode 8.7581171549

4096: KV 9.79x
  C1  TTFT 8.3055768646  decode 56.1285205183
  C4  TTFT 16.8877389152 decode 14.3152836633
  C10 TTFT 41.4841257613 decode 7.3619789775

8192: KV 5.93x
  C1  TTFT 7.7221944593  decode 55.9848131280
  C4  TTFT 15.9963459531 decode 13.9316080768
  C10 TTFT 38.5664838441 decode 6.5076314283
```

现场最终选择 256K、C16、4096、target-only。256K 约 19.58x KV capacity 是由
512K/4096 的 9.79x 比例推导，并非独立启动日志。

## 客户端观察

- New API 在服务绑定 127.0.0.1 时无法从容器连接；改绑 172.17.0.1 后接入成功；
- Claude Code 经 New API 时大量请求只输出约 7 token 并中途结束；
- Codex CLI 使用相同后端没有该问题，长上下文和连续工具调用均可工作；
- 因而 Codex + Responses 被设为优先支持链路，Claude 转换路径列为兼容性受限。

## 最终选择

```text
mode=target-only
max_model_len=262144
max_num_seqs=16
max_num_batched_tokens=4096
gpu_memory_utilization=0.92
execution=graph
tensor_parallel_size=8
host=172.17.0.1
port=8005
```

仍需补做：最终组合的 C16 短矩阵、逐级长上下文、1 小时与 24 小时稳定性，并把所有
原始 JSON/CSV/日志归档回项目。
