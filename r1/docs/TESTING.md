# 测试与证据

## 静态和 API 验收

```bash
./run-tests.sh
DSV4_SCHEME=two ./run-tests.sh
```

完整入口先校验并加载预编译镜像；不会在目标机执行任何 build。

API 验收覆盖模型别名、tokenizer 端点、普通 Chat、JSON object/schema、guided
JSON、流式/非流式 tool call、Responses API，以及 Anthropic Messages 的
count_tokens、trailing-system、流式和 tool use。原始 DSML 标记或未声明工具名
一旦泄漏即失败。

## 指定源码回归

```bash
r1/scripts/run_source_tests.sh
```

预编译的派生测试镜像完全离线运行，并对已安装的 R1 package 原样测试：

```text
tests/parser/engine/test_deepseek_v4.py
tests/parser/engine
tests/tokenizers_/test_deepseek_v4.py
tests/models/test_deepseek_v4_mega_moe.py
tests/models/test_deepseek_v4_target_post_load_contract.py
tests/entrypoints/anthropic/test_anthropic_messages_conversion.py
```

前三条和 MegaMoE 是原附件逐字要求的路径；生命周期契约覆盖 target-only 的
post-load 语义，最后一条覆盖新增 Claude/Anthropic 转换。每条命令保存完整日志和
JUnit。MegaMoE suite 在目标 A100 上禁止 skip；collection error、失败、缺少
JUnit 或 skip 都会阻断完整门禁。

## 完整 target-only 工具矩阵

```bash
r1/scripts/run_tool_matrix.sh
```

执行 `stream=false/true × tool_choice=auto/required/none × thinking=off/on ×
concurrency=1/4/8` 的完整 36 个 case、156 个同步请求。每个需要工具调用的请求
同时验证 string、boolean、integer、number、array、object，以及带真实 heredoc
的多行 shell 字符串。命令只传给 `bash -n` 做语法解析，绝不执行。原始请求和
响应只在失败时写入权限 0600 的 `failures.jsonl`，不会作为 assistant 历史回写。

## 500 请求稳定性门禁

```bash
r1/scripts/run_stability.sh
```

固定 500 请求、约 10K token 上下文；方案一并发 32，方案二并发 16。轮换 Chat、Responses、
Anthropic 流式/非流式 tool call。门禁要求零失败且实际观察到所选方案的完整并发；
同时记录服务前后 `/v1/models`、容器 restart/OOM 状态和 EngineCore PID/kernel
start-ticks 连续性。summary 分别记录 structured success rate、原始 DSML、参数
类型、未声明工具、shell 损坏和 EngineCore restart count；所有失败原样保存。

## 双方案性能矩阵

```bash
./benchmark_one.sh
./benchmark_two.sh
```

精确网格为：

- 上下文：10K 到 200K，步进 10K，共 20 档；
- 方案一并发：1 到 16，步进 1，共 16 档；320 cells、2,720 个请求/轮；
- 方案二并发：1 到 8，步进 1，共 8 档；160 cells、720 个请求/轮。

每个上下文先通过 `/tokenize` 二分校准；每个请求使用独立 256-bit
`cache_salt`，避免前缀缓存污染。采集 realized prompt tokens、TTFT
p50/p95/p99、端到端 latency p50/p95/p99、输入/输出/总吞吐和错误证据。
CSV 每格完成后原子替换，默认目录固定为
`RUNTIME_BASE/one/results/performance-one` 与
`RUNTIME_BASE/two/results/performance-two`，重跑自动续跑。只有显式传
`--overwrite` 才会重新开始。

## artifact 与证据包

```bash
r1/scripts/generate_result_artifact.sh
r1/scripts/collect_results.sh
```

artifact 生成器按方案拒绝任何不是完整 20×16 或 20×8 cross product 的 CSV，
也拒绝缺失指标、
请求计数不一致或 prompt token 超出容差却标记为 complete 的 cell。证据包包含
运行结果、日志、镜像 digest/inspect、三层镜像（base、R1、source-test）的
`pip freeze`、GPU 信息、锁定 manifest、失败样本和可移植报告；
不会包含 `secrets.env` 或 API key。最终性能 HTML 在工作机通过：

```bash
export DATA_ANALYTICS_PLUGIN_ROOT=/path/to/data-analytics/plugin
r1/reports/build_report.sh /path/to/performance-matrix.csv
```

目标侧已经通过 Docker 自动生成可直接查看的 HTML；上面的工作机步骤需要
Docker、Node 和 Data Analytics plugin，用于 canonical builder/Chromium 最终
发布复核，不需要宿主机 Python。初始 pending 页面展示覆盖率和完整 320/160-cell 表；
完成态页面展示 P95 TTFT、总吞吐两张热力图及该表。
