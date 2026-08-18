# 测试方法与指标定义

## 测试顺序

每种模式都按以下顺序推进：

1. 模型 metadata 与离线包预检；
2. 32K eager 功能 smoke；
3. CUDA Graph 短请求；
4. 单路长上下文；
5. 逐级增加并发；
6. 10 分钟、1 小时、可选 24 小时稳定性；
7. 重启恢复与 `--network none` 验收。

target-only 与 DSpark 使用全部 8 张卡，必须停止一个后再启动另一个。

## Smoke 覆盖

```bash
./target-only/smoke-test.sh
./dspark/smoke-test.sh
```

测试程序覆盖：

- `GET /v1/models`；
- `POST /v1/chat/completions`；
- 适用时的 `POST /v1/completions`；
- 中文问答、数学、Python、TCL/EDA、JSON；
- low/high/max reasoning effort；
- reasoning 字段；
- 多轮、长 prompt、SSE 流式输出；
- DeepSeek V4 本地 encoding 与 parser。

2026-08-13 target-only smoke 为 13 项通过、1 项可选失败、0 项必需失败。可选失败是
字段兼容问题：返回对象包含 `reasoning` 而不是 `reasoning_content`。

## Benchmark 用法

快速矩阵：

```bash
./target-only/benchmark.sh
```

指定矩阵：

```bash
PROMPT_LENGTHS=1024,11000,32768,131072 \
OUTPUT_LENGTHS=128,512,1024,2048 \
CONCURRENCY_LEVELS=1,2,4,8 \
BENCHMARK_REPEATS=1 \
./target-only/benchmark.sh
```

256K 必须在 256K profile 启动后单独添加：

```bash
PROMPT_LENGTHS=262000 \
OUTPUT_LENGTHS=128 \
CONCURRENCY_LEVELS=1 \
GPU_METRICS_INTERVAL=0.2 \
./target-only/benchmark.sh
```

## 指标定义

| 字段 | 定义 | 注意事项 |
|---|---|---|
| TTFT | 客户端发起请求到第一个非空流式文本 chunk | 包含排队、网络和 prefill |
| prefill latency proxy | 与 TTFT 相同 | 不是纯 kernel prefill 时间 |
| prompt tokens/s proxy | prompt token 数 / TTFT | 同样包含调度和传输 |
| decode TPS | `(completion_tokens-1) / 首末文本 token 时间跨度` | 单请求指标 |
| `decode_tps_mean` | 成功请求的单请求 decode TPS 算术平均 | 不能当 aggregate TPS |
| mean ITL | decode 时间跨度 / `(completion_tokens-1)` | 秒/token |
| aggregate throughput | 并发组总 completion token / 整组 wall span | wall span 包含 TTFT |
| E2E | 请求发出到 `[DONE]` | 包含所有阶段 |
| KV maximum concurrency | 启动日志按 `max_model_len` 计算的驻留容量 | 不代表同时快速 prefill |

vLLM 日志中的 `Avg prompt/generation throughput` 是 10 秒滑动窗口，适合观察服务状态，
不适合作为单请求最终性能结论。

## Prompt 可比性

如果有 MiniMax-M2.7 的原始 11K prompt：

```bash
BASELINE_PROMPT_FILE=/absolute/path/to/original-11k.txt \
./target-only/benchmark.sh
```

如果没有，脚本通过本地 `/tokenize` 和 `/detokenize` 生成精确 token 数的可复现 prompt，
并明确标记它不是历史 MiniMax prompt。历史参考只保留为：MiniMax-M2.7，230B total /
10B activated，约 11K 输入，TTFT 约 15 秒。不同 prompt、输出和模型不能做严格速度倍数。

## `max_num_batched_tokens` 调优

现场在 512K、`max_num_seqs=10`、0.92、CUDA Graph 下测试了 2048/4096/8192。测试
prompt 为 65,536、output 32，并发 1/4/10。精确结果见
[性能报告](../reports/performance-report-2026-08-13.md)。

4096 相比 2048 将 TTFT 降低约 9.1%/10.8%/17.8%，但 512K KV maximum concurrency
从 14.21x 降至 9.79x。8192 继续降低 TTFT，但容量降至 5.93x，且高并发单请求 decode
继续下降。因此最终选择 4096 作为 latency/capacity 折中。

## DSpark 验证

必须同时满足：

```text
SpeculativeConfig(method='dspark', num_spec_tokens=5)
DSpark draft model loaded: 96 params
SpecDecoding metrics 持续增加
```

日志中 `mtp.*` 是 checkpoint 权重命名，运行 method 仍必须为 `dspark`。不要配置第二个
draft model，也不要写 `method=mtp`。

DSpark acceptance 会随工作负载变化；现场快照从 80.0% 降到 25.6%，平均接受长度从
5.00 降到 2.28。不能只用最有利的一条日志代表长期收益。

## 稳定性与恢复

```bash
STABILITY_MINUTES=10 ./target-only/stability-test.sh
STABILITY_MINUTES=60 STABILITY_CONCURRENCY=8 ./target-only/stability-test.sh
STABILITY_MINUTES=1440 STABILITY_CONCURRENCY=8 ./target-only/stability-test.sh
./scripts/restart_recovery_test.sh target-only
```

检查显存是否持续增长、CUDA/NCCL/Graph 错误、空输出、乱码、请求错误与 API 健康。

## 保存目标机原始证据

当前项目只归档了操作者粘贴的终端摘录。下一次测试后，应把原始文件复制回来：

```bash
tar -czf /tmp/dsv4-field-results-$(date -u +%Y%m%dT%H%M%SZ).tar.gz \
  benchmarks/results \
  logs/target-only logs/dspark \
  run/target-only.launch run/dspark.launch \
  run/target-only.startup.env run/dspark.startup.env \
  reports/model-verification-*.json
```

归档前检查其中没有 API Token 或业务敏感 prompt。导入本项目后，现场数据证据等级才能从
`B` 升为 `A`。

## A/B 对比

```bash
python3 scripts/compare_results.py \
  benchmarks/results/benchmark-target-only-TIMESTAMP.json \
  benchmarks/results/benchmark-dspark-TIMESTAMP.json \
  --markdown reports/benchmark-summary.md \
  --csv benchmarks/results/comparison.csv
```

只有 prompt、output、并发、execution mode、显存配置和 warmup 都一致时才写速度倍数。
