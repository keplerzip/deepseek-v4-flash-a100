# DeepSeek V4 Flash 0731：8×A100 现场性能报告

报告整理日期：2026-08-16；测试日期：2026-08-13；目标机：Ubuntu 22.04.4、
8×A100-SXM4-80GB、TP=8、Driver 580.159.04。

汇报用横版总览图：
[deepseek-v4-flash-a100-performance-summary-2026-08-13.png](deepseek-v4-flash-a100-performance-summary-2026-08-13.png)

## 技术摘要

**target-only 适合作为当前生产默认，最终选择 256K、并发上限 16、
`max_num_batched_tokens=4096`、显存比例 0.92 和 CUDA Graph。** 在 307,200 最大上下文、
C12、1K 输入/1K 输出的已测组合中，12/12 请求成功，TTFT P50/P95 为 0.779/1.302 秒，
单请求 decode 平均 36.55 tok/s，整组 aggregate throughput 为 425.34 tok/s。单路
300K/128 也成功，TTFT 73.15 秒、decode 30.75 tok/s。

**4096 是延迟与 KV 容量的折中，不是单纯最快值。** 在 512K/C10 调优中，相比 2048，
4096 把 65,536-token prompt 的 TTFT 降低 9.1%–17.8%，但 512K 的日志 KV maximum
concurrency 从 14.21x 降到 9.79x。8192 再降低约 5.3%–7.0% TTFT，却把容量进一步
降到 5.93x，并降低高并发单请求 decode，因此未选用。

**DSpark 已确认真实启用且 decode 很快，但尚不满足生产稳定性要求。** 日志确认
`method=dspark`、5 个 speculative tokens、同一模型路径、96 个 draft 参数并完成 DSpark
Graph capture。单路 decode 在 1K/11K/128K prompt 下分别达到 341.42/301.33/218.84
tok/s；260K/C6/128 也以 6/6 完成、单请求 decode 平均 48.89 tok/s。不过 acceptance
随负载从 80.0% 降到 25.6%，0.95 显存配置发生 OOM，另一长上下文 Graph 组合发生
illegal memory access。DSpark 不提升 prefill，故暂列实验模式。

**证据仍有边界。** 本报告的精确数值来自操作者粘贴的终端输出，保留了原始结果文件名，
但目标机 JSON/GPU CSV 尚未复制回项目，证据等级为 B。多数矩阵只重复一次，最终
256K/C16/4096 组合尚缺 1 小时和 24 小时完整稳定性结果。

## target-only 已证明短并发和 300K 单路可用

生产判断主要依赖两次 CUDA Graph 运行。第一组验证短 prompt 的并发吞吐，第二组验证
接近 300K 的单请求可用性；两组不是同一 workload，不能用来外推 16 路 256K 同时快速
prefill。

| 最大上下文 | Prompt / Output | 并发 | 成功 | TTFT P50 | Decode TPS（单请求均值） | Aggregate TPS | E2E P50 |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 307,200 | 1,024 / 1,024 | 12 | 12/12 | 0.779s | 36.55 | 425.34 | 28.84s |
| 307,200 | 300,000 / 128 | 1 | 1/1 | 73.148s | 30.75 | 未保存 | 未保存 |

第一组启动日志记录：模型加载占 19.81GiB、可用 KV cache 49.83GiB、GPU KV cache
5,674,802 tokens，按 307,200 上下文计算 maximum concurrency 为 18.47x，并成功完成
mixed prefill/decode 与 full decode CUDA Graph capture。

按 512K/4096 的 9.79x KV 容量等比例折算，256K 约为 19.58x。最终调度上限 16 比
这一推导容量低约 18.3%。这是容量规划值，不是 C16×256K 的性能承诺。

## 8 张 GPU 均参与，GPU0 的采样利用率偏低

在 target-only C12 短请求运行期间，各卡显存峰值集中在 77,211–77,221MiB，说明 TP=8
分片对称。GPU1–7 的平均利用率约 97.3%，GPU0 为 74.2%，导致八卡简单平均 94.4%。
平均功耗约 167W，瞬时最大值 300.7–311.4W。低平均功耗不代表 TP 未启用；该运行只有
约一分钟活跃样本，且 workload 包含同步、调度与通信阶段。

| GPU | Util avg / P95 | Power avg / max | Memory peak |
|---:|---:|---:|---:|
| 0 | 74.2% / 97.0% | 164.4W / 306.1W | 77,213MiB |
| 1 | 96.4% / 99.0% | 168.5W / 307.0W | 77,219MiB |
| 2 | 97.8% / 98.0% | 168.8W / 300.7W | 77,213MiB |
| 3 | 96.3% / 99.0% | 168.2W / 302.8W | 77,219MiB |
| 4 | 98.4% / 99.0% | 166.3W / 308.5W | 77,219MiB |
| 5 | 97.1% / 99.0% | 165.5W / 302.8W | 77,221MiB |
| 6 | 97.0% / 99.0% | 169.5W / 311.4W | 77,213MiB |
| 7 | 98.2% / 99.0% | 165.0W / 301.3W | 77,211MiB |

`gpu_memory_utilization=0.92` 是 vLLM 的规划比例，不是进程显存硬上限。模型、Graph、
通信和其他非 KV 分配会使 `nvidia-smi` 的实际 used memory 与 0.92×80GiB 不完全一致。

## 4096 在 TTFT 与容量之间最均衡

调优固定 max model len 512K、max sequences 10、GPU memory 0.92、CUDA Graph，使用
65,536-token prompt、32-token output、并发 1/4/10，每组重复一次。

| Batched tokens | 512K KV capacity | C1 TTFT / Decode | C4 TTFT / Decode | C10 TTFT / Decode |
|---:|---:|---:|---:|---:|
| 2,048 | 14.21x | 9.139s / 56.03 | 18.932s / 16.86 | 50.462s / 8.76 |
| 4,096 | 9.79x | 8.306s / 56.13 | 16.888s / 14.32 | 41.484s / 7.36 |
| 8,192 | 5.93x | 7.722s / 55.98 | 15.996s / 13.93 | 38.566s / 6.51 |

4096 相对 2048 的 TTFT 改善随并发增大，在 C10 达 17.8%；代价是日志 KV capacity
降低 31.1%，C10 单请求 decode 均值降低约 15.9%。8192 相对 4096 只再改善
5.3%–7.0% TTFT，却继续损失 39.4% KV capacity，并使 C10 单请求 decode 再降低约
11.6%。因此 4096 更符合“多人使用且保持较好 prefill”的目标。

## DSpark 的 decode 收益明显，但显存和 Graph 风险也更高

DSpark 的成功单路数据来自 256K profile、5 speculative tokens、greedy、CUDA Graph、
显存比例 0.95。该组前三项完成，第四项在 260K/512 时 OOM。

| Prompt / Output | 并发 | 成功 | TTFT P50 | Decode TPS（单请求均值） | 结果 |
|---:|---:|---:|---:|---:|---|
| 1,024 / 512 | 1 | 1/1 | 0.158s | 341.42 | 通过 |
| 11,000 / 512 | 1 | 1/1 | 1.344s | 301.33 | 通过 |
| 131,072 / 512 | 1 | 1/1 | 25.028s | 218.84 | 通过 |
| 260,000 / 512 | 1 | 0/1 | — | — | OOM，API shutdown |

OOM 时多卡尝试分配约 400,556,032 bytes，而空闲显存只有约 7–9MiB。0.95 把可见显存
大部分交给模型与 KV 后，没有给 draft、Graph 和临时张量保留足够 workspace。

其他长上下文测试进一步说明执行模式的影响：

| 模式 / 关键配置 | Prompt / Output | 并发 | 成功 | TTFT P50 | Decode TPS 均值 | 结论 |
|---|---:|---:|---:|---:|---:|---|
| Graph / 手工 16GiB KV | 260K / 128 | 2 | 2/2 | 60.433s | 105.80 | 完成 |
| Graph / 手工 16GiB KV | 260K / 128 | 3 | 1/3 | 61.269s | 20.19 | illegal memory access，进程退出 |
| Eager / 0.80 | 260K / 128 | 3 | 3/3 | 119.753s | 27.32 | 稳定但明显较慢 |
| Graph / 0.80、C6 profile | 260K / 128 | 6 | 6/6 | 180.476s | 48.89 | 单次矩阵通过 |

最后一行证明 C6 可以完成一次长上下文矩阵，但不抵消另一 Graph 组合的引擎崩溃；两者
KV 配置不同，不能合并成“DSpark Graph 已稳定”的结论。

## DSpark acceptance 对工作负载敏感

服务日志中的五个快照显示 acceptance 并不固定：

| 快照 | Mean acceptance length | Accepted / Drafted | Avg draft acceptance |
|---:|---:|---:|---:|
| 1 | 5.00 | 24 / 30 | 80.0% |
| 2 | 5.00 | 8 / 10 | 80.0% |
| 3 | 3.31 | 296 / 640 | 46.2% |
| 4 | 3.17 | 26 / 60 | 43.3% |
| 5 | 2.28 | 409 / 1,595 | 25.6% |

DSpark 的收益来自 decode 阶段一次接受多个 draft token；它不减少 260K prompt 的
prefill 计算。acceptance 下降时，draft 开销仍存在而接受收益变小，所以应该按实际
Codex/代码任务分布做长时间统计，而不是只保留 80% 的短样本。

## 功能正确性与客户端结果

模型检查确认 48 个 shard、72,317 个权重 key、DeepSeek V4 架构与 `mtp.*` 权重，目录
155.44GiB。target-only smoke 的所有必需项通过，包括中文、数学、Python、TCL/EDA、
JSON、多轮、长 prompt 和流式输出。

reasoning 字段存在兼容差异：该 fork 返回 `message.reasoning`，而不是测试最初期望的
`reasoning_content`。复杂推理在 `max_tokens=1024` 时以 `finish_reason=length` 结束、
正文为空，表示预算耗在 reasoning 阶段。

Codex CLI 经 New API Responses 在实际长任务中工作正常并能使用长上下文。Claude Code
经 New API 的协议转换却多次只输出约 7 token 后结束。因为相同 vLLM 后端通过 Codex
正常，该现象更符合网关 stop/tool/stream 转换问题，而不是服务端固定截断。

## 数据范围与指标口径

- TTFT：客户端请求发出到第一个非空流式文本 chunk，包含排队、传输与 prefill；
- prefill TPS：prompt token / TTFT，仅为 proxy；
- decode TPS：单请求 `(completion_tokens-1)` 除以首末输出 token 时间跨度；
- aggregate TPS：并发组总 completion token 除以整组 wall span，包含 TTFT；
- KV maximum concurrency：vLLM 启动日志基于 max model len 的驻留容量；
- 所有 benchmark 均在 TP=8、FP8 KV、同一模型 checkpoint 和固定 fork 上运行；
- prompt 默认由本地 tokenize/detokenize 生成；未找到原 MiniMax 11K prompt。

历史 MiniMax 参考保持原样：MiniMax-M2.7，230B total / 10B activated，约 11K 输入，
TTFT 约 15 秒。由于原 prompt、执行模式和输出条件不同，本报告不计算 DeepSeek 相对
MiniMax 的速度倍数。

## 方法与可复现性

请求由 `scripts/benchmark_api.py` 以 SSE 流式方式发出，并从 usage 和本地高精度时钟
计算指标。`scripts/collect_metrics.py` 并行采集各 GPU 显存、利用率和功耗；
`scripts/finalize_benchmark.py` 将 GPU 峰值、启动时间和日志诊断合并回 JSON。

结构化摘录保存在：

- `reports/data/field-benchmark-summary-2026-08-13.csv`；
- `reports/data/target-gpu-summary-2026-08-13.csv`；
- `reports/data/dspark-acceptance-snapshots-2026-08-13.csv`。

## 局限性、质量风险与稳健性检查

1. **原始文件缺失（中等风险，高置信）。** 结果文件名已保留，但 JSON/GPU CSV/完整日志
   未回传；当前证据等级 B，无法重新计算 percentile 或检查每个 request。
2. **重复次数为 1（中等风险，高置信）。** 无法估计方差、热态漂移或 P95 稳定性。
3. **最终组合未完整 soak（高风险，高置信）。** 256K/C16/4096 是基于容量和相邻矩阵
   选择，尚没有 C16 长 prompt 与 1h/24h 结果。
4. **DSpark 组合发生变化（高风险，高置信）。** 0.95、0.80、手工 16GiB KV、Graph 与
   eager 不能汇总成一条单一性能曲线。
5. **没有严格 target/DSpark A/B（中等风险，高置信）。** 缺少同 prompt、output、并发、
   Graph、显存与 warmup 的成对结果，不能计算可靠 speedup。
6. **GPU0 平均利用率异常低（低至中等风险，中等置信）。** 峰值正常且 8 卡显存对称，
   但需要更长采样确认是 rank0 调度特征还是监控/NUMA 问题。
7. **客户端链路不是同一协议（中等风险，高置信）。** Codex Responses 成功不能自动证明
   Anthropic messages 转换正确。

## 建议的生产动作

1. 使用已固化的 target-only 256K/C16/4096/0.92/Graph 配置；
2. 先跑 1K 输出 512 的 C1/2/4/8/16，再跑 256K 的 C1/2，确认无回归；
3. 完成 C8 混合长短请求的 1 小时稳定性，再完成 24 小时 soak；
4. 把原始 JSON、GPU CSV、launch manifest 和日志归档回项目，将证据提升到 A；
5. DSpark 仅在维护窗口以 0.80/C6 继续测试，不使用 0.95 或手工 16GiB KV；
6. 对真实 Codex workload 统计 DSpark acceptance 分布，再判断 decode 收益是否抵消稳定性
   和显存成本；
7. New API 生产入口优先提供 Responses；Claude 转换问题单独做协议追踪。

## 仍需回答的问题

- target-only Graph 下同一 1K/512/C1 的单流 decode 是否接近社区 85.5 tok/s 参考？
- 最终 256K/C16/4096 在 1 小时和 24 小时内是否有显存增长或尾延迟恶化？
- GPU0 的长期利用率是否仍显著低于 GPU1–7？
- DSpark 在真实代码代理请求中的 acceptance P50/P95 是多少？
- `message.reasoning` 与 New API Responses 的 reasoning 映射是否能端到端保留？
