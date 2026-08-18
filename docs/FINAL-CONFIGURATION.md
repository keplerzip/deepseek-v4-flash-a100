# 最终定型方案

## 结论

当前生产定型方案是 **target-only、8×A100、TP=8、256K 上下文、C16、4096、
0.92、CUDA Graph**。DSpark 保留为实验模式，不属于生产定型方案。

| 项目 | 最终值 | 配置来源 |
|---|---:|---|
| 模式 | target-only | `config/target-only.env` |
| GPU | 0–7，共 8 张 A100-SXM4-80GB | `config/common.env` |
| Tensor Parallel | 8 | `config/common.env` |
| 最大上下文 | 262,144 tokens（256K） | `config/profiles/256k.env` |
| `max_num_seqs` | 16 | `config/profiles/256k.env` |
| `max_num_batched_tokens` | 4,096 | `config/production-target.env` |
| GPU memory utilization | 0.92 | `config/production-target.env` |
| KV cache dtype | FP8 | `scripts/mode_action.sh` |
| 执行模式 | CUDA Graph | `config/production-target.env` |
| 端口 | 8005 | `config/production-target.env` |
| 默认监听地址 | 172.17.0.1 | `config/production-target.env` |
| 服务模型名 | `deepseek-v4-flash-0731-target` | `config/target-only.env` |
| vLLM | `haosdent/vllm@f8ea5bb163c161ef38b401d055cc5fd4a934091a` | `config/common.env` |

target-only 的最终命令不包含 `--speculative-config`。模型从宿主机只读挂载，不进入镜像。

## 配置优先级

最终值由以下链路组合：

```text
start-production.sh
  -> config/production-target.env
  -> target-only/start.sh
  -> scripts/mode_action.sh
  -> config/common.env
  -> config/model.env
  -> config/target-only.env
  -> config/profiles/256k.env
```

`start-production.sh` 会显式导出生产参数，避免当前 shell 残留的旧调优变量污染启动。
检查而不启动：

```bash
DSV4_CONFIG_ONLY=1 source ./start-production.sh
```

正式启动：

```bash
source ./start-production.sh
```

确认容器实际参数：

```bash
./target-only/status.sh
sudo -n docker inspect dsv4-target-only-f8ea5bb \
  --format '{{json .Config.Cmd}}' | python3 -m json.tool
```

## 选择依据

- target-only 在 307,200 最大上下文、1K 输入/1K 输出、C12 下完成 12/12 请求，
  aggregate throughput 为 425.34 tok/s；
- 单路 300K/128 完成，TTFT 73.15 秒，decode 30.75 tok/s；
- 512K 调优中，4096 相比 2048 显著改善高并发 TTFT，同时比 8192 保留更多 KV 容量；
- 256K/C16 的容量来自 512K/4096 启动日志的 9.79x 等比例规划，取 16 留出余量；
- DSpark 虽然单路 decode 更快，但出现过 0.95 OOM 和另一 Graph 组合的 illegal memory
  access，且不提升长 prompt prefill，因此不作为无人值守生产默认值。

完整数据见 [A100 性能报告](../reports/performance-report-2026-08-13.md)。

## 验收边界

“最终定型”表示当前选择并已固化的生产参数，不代表已经取得完整 SLA。仍需补齐：

- 最终组合的 C1/2/4/8/16 同口径矩阵；
- 256K 长上下文逐级并发验证；
- 1 小时稳定性和 24 小时 soak；
- 重启恢复后的重复性能与目标机原始 JSON/CSV/日志归档。

详情见 [已知限制](KNOWN-LIMITATIONS.md)。
