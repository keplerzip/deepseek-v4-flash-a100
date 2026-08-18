# 生产运行与日常操作

## 当前生产入口

```bash
cd /ai/services/deepseek-v4-flash-a100/deploy
source ./start-production.sh
```

入口读取 `config/production-target.env`，并显式覆盖容易被旧调优 shell 污染的运行参数。
它不会设置 `FORCE_START=1`。

| 变量 | 生产值 | 作用 |
|---|---:|---|
| `PROFILE` | `256k` | `max_model_len=262144` |
| `EXECUTION_MODE` | `graph` | 启用 CUDA Graph |
| `GPU_MEMORY_UTILIZATION` | `0.92` | 模型、KV 与运行时显存预算 |
| `MAX_NUM_BATCHED_TOKENS` | `4096` | 单轮调度 token 上限 |
| `TARGET_MAX_NUM_SEQS` | `16` | 调度中的请求数上限 |
| `TENSOR_PARALLEL_SIZE` | `8` | 8 卡张量并行 |
| `PORT` | `8005` | 两个模式共用、互斥 |

不要在 `config/*.env` 中写入裸的 `--enable-chunked-prefill` 一类命令行片段；env 文件会被
shell source，裸参数会被当成命令。项目现在对 `max_num_batched_tokens` 和
`kv_cache_memory_bytes` 使用独立变量并由启动器组装参数。

## 状态和实际参数

```bash
./target-only/status.sh
sudo -n docker inspect dsv4-target-only-f8ea5bb \
  --format '{{json .Config.Cmd}}' | python3 -m json.tool
cat run/target-only.launch
```

`status.sh` 会显示 profile、执行模式、最大上下文、并发、显存比例、batched tokens、
API 健康状态和 8 张 GPU 的即时状态。`run/target-only.launch` 是本次启动的可审计命令。

## 日志

```bash
./target-only/logs.sh --tail 200
./target-only/logs.sh --tail 200 --follow
```

重点关注：

```text
Model loading took
Available KV cache memory
GPU KV cache size
Maximum concurrency
Capturing CUDA graphs
Application startup complete
CUDA error
illegal memory access
NCCL
OOM
```

vLLM 每 10 秒的 `Avg generation throughput` 是时间窗口内整个引擎的瞬时统计，不等于
单请求最终 TPS。性能结论以 benchmark JSON 的 client-side 指标为准。

## GPU guard 与 DCGM

目标机的 `gpu-monitor-dcgm-exporter` 请求全部 GPU，但只做 NVML/DCGM 监控。生产配置用
精确容器名将它加入 allowlist：

```bash
GPU_GUARD_ALLOWED_CONTAINERS=gpu-monitor-dcgm-exporter
```

allowlist 只跳过该容器的 `DeviceRequests` 判断。以下项目仍会阻止启动：

- `nvidia-smi` 中的任何 compute process；
- 其他请求 GPU 的容器；
- 8005 已监听；
- `run/dsv4-a100.lock` 已存在；
- GPU 不是完整的 0–7 八张 A100。

不要把 `FORCE_START=1` 写入生产配置。确需临时绕过时必须先人工确认输出；脚本即使绕过
也不会 kill 任何进程。

## New API 与客户端

服务端健康检查：

```bash
curl -fsS http://172.17.0.1:8005/v1/models
```

New API 上游地址通常为：

```text
http://host.docker.internal:8005/v1
```

推荐客户端为 Codex CLI，详见 [Codex 指南](CODEX-CLI.md)。Codex 通过
Responses 链路已完成真实长任务；Claude Code 经协议转换的 7-token 中断不能用增加 KV
或上下文解决。

## 生产性能抽查

先用短 prompt 验证并发，不要一开始提交 16 个 256K prompt：

```bash
PROMPT_LENGTHS=1024 \
OUTPUT_LENGTHS=512 \
CONCURRENCY_LEVELS=1,2,4,8,16 \
BENCHMARK_REPEATS=1 \
./target-only/benchmark.sh
```

再逐步检查长上下文：

```bash
PROMPT_LENGTHS=262000 \
OUTPUT_LENGTHS=128 \
CONCURRENCY_LEVELS=1,2 \
BENCHMARK_REPEATS=1 \
GPU_METRICS_INTERVAL=0.2 \
./target-only/benchmark.sh
```

`max_num_seqs=16` 允许请求排队和调度；KV 可以驻留不等于 16 路都能同时快速 prefill。

## 稳定性

```bash
STABILITY_MINUTES=10 ./target-only/stability-test.sh
STABILITY_MINUTES=60 STABILITY_CONCURRENCY=8 ./target-only/stability-test.sh
./scripts/restart_recovery_test.sh target-only
```

上线前至少完成 1 小时测试，并检查显存增长、空输出、CUDA/NCCL 错误、服务健康和重启
恢复。24 小时 soak 仍是推荐的最终门槛。

## 停止

```bash
./stop-production.sh
```

停止后日志会保存到 `logs/target-only/`，公共锁只在确认属于本模式时删除。
