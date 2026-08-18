# deepseek-v4-flash-a100

DeepSeek-V4-Flash-0731 在 8×A100-SXM4-80GB 上的离线 vLLM 部署、测试与运维项目。

这是已经在目标机完成模型加载、API、功能 smoke、长上下文和性能测试的离线部署项目。
目标环境为 Ubuntu 22.04.4、8×A100-SXM4-80GB、Driver 580.159.04，运行固定的
`haosdent/vllm@f8ea5bb163c161ef38b401d055cc5fd4a934091a`。项目不下载模型、不升级
Driver，也不会停止或删除 MiniMax、其他容器或 Python 环境。

当前生产建议是 **target-only**。DSpark 已证明能够加载并产生 speculative acceptance，
但在 256K 极限显存与 CUDA Graph 组合下出现过 OOM 和 illegal memory access，因此保留为
实验模式，不作为无人值守生产默认值。

## 公开仓库范围

本 GitHub 仓库是 **source-only** 发行：只包含部署脚本、配置模板、测试工具、文档、
固定版本清单、许可证和脱敏后的现场性能报告。以下内容不会上传 GitHub：

- DeepSeek 模型权重及 `encoding/`、`inference/` 辅助文件；
- 约 25GB 的容器镜像 tar、wheelhouse、编译产物和 vendored Git checkout；
- 本机 `config/model.env`、运行日志、PID/锁、原始环境审计和临时 benchmark 结果。

需要完整断网部署时，应在联网构建机运行准备脚本，或单独取得与本项目固定版本相匹配的
离线 artifact bundle。不要把 GitHub source checkout 误认为已经包含运行镜像。

## 项目结构

```text
config/          固化配置与上下文 profiles
target-only/     生产推荐模式的启停、日志与测试
dspark/          实验性 speculative 模式
scripts/         公共运行、验证、构建、打包与 benchmark 工具
common/          固定版本清单、许可证和离线构建配方
benchmarks/      可复现输入与结果目录约定
reports/         脱敏后的 A100 现场数据、报告与汇报图
docs/            部署、运维、测试、排障和架构文档
```

大文件和运行时生成目录的边界见
[完整项目结构说明](docs/PROJECT-STRUCTURE.md)。

## A100 现场实测性能

测试环境统一为 Ubuntu 22.04.4、8×A100-SXM4-80GB、TP=8、Driver 580.159.04、
FP8 KV cache。以下均为 2026-08-13 现场实测，不是社区参考值：

| 模式 / 场景 | Prompt / Output | 并发 | 成功 | TTFT P50 | Decode TPS | Aggregate TPS |
|---|---:|---:|---:|---:|---:|---:|
| target-only Graph | 1,024 / 1,024 | 12 | 12/12 | 0.779s | 36.55/请求 | 425.34 |
| target-only Graph | 300,000 / 128 | 1 | 1/1 | 73.148s | 30.75/请求 | 未保存 |
| DSpark Graph | 1,024 / 512 | 1 | 1/1 | 0.158s | 341.42/请求 | 未保存 |
| DSpark Graph | 11,000 / 512 | 1 | 1/1 | 1.344s | 301.33/请求 | 未保存 |
| DSpark Graph | 131,072 / 512 | 1 | 1/1 | 25.028s | 218.84/请求 | 未保存 |
| DSpark Graph, 0.80/C6 | 260,000 / 128 | 6 | 6/6 | 180.476s | 48.89/请求 | 未保存 |

target-only 在 512K/C10、65,536-token prompt 的 `max_num_batched_tokens` 调优中，
2048/4096/8192 的 C10 TTFT 分别为 50.462/41.484/38.566 秒；4096 在 prefill 延迟、
单请求 decode 与 KV 容量之间最均衡，因此成为生产默认。

这些数字多数只重复一次，证据等级为 B。DSpark 还出现过 0.95 显存下 OOM 和另一组合的
CUDA illegal memory access；最终 target-only 256K/C16/4096 配置尚未完成 1h/24h soak，
不能把上表外推为 SLA。完整条件、指标口径、失败记录和 GPU 数据见
[性能报告](reports/performance-report-2026-08-13.md)与
[横版汇报图](reports/deepseek-v4-flash-a100-performance-summary-2026-08-13.png)。

## 生产配置

| 项目 | 固化值 |
|---|---:|
| 模式 | target-only |
| GPU / TP | GPU 0–7 / TP=8 |
| 最大上下文 | 262,144 tokens（256K） |
| `max_num_seqs` | 16 |
| `max_num_batched_tokens` | 4,096 |
| KV dtype | FP8 |
| GPU memory utilization | 0.92 |
| 执行模式 | CUDA Graph |
| 监听地址 | `172.17.0.1:8005` |
| 模型名 | `deepseek-v4-flash-0731-target` |

`max_num_seqs=16` 是调度上限，不代表 16 个 256K prompt 能同时快速 prefill。基于
512K/4096 启动日志折算的 256K KV 驻留容量约为 19.58 路；16 路留有约 18% 的
KV 容量余量，但最终组合仍应完成持续并发验收。

参数来源、覆盖顺序和选择依据集中记录在
[最终定型方案](docs/FINAL-CONFIGURATION.md)。

## 一键启动

第一次使用先完成离线导入和预检：

```bash
cd /ai/services/deepseek-v4-flash-a100/deploy
./scripts/verify_offline_bundle.sh --artifact-only
./scripts/install_offline.sh
./scripts/target_acceptance.sh preflight
```

以后只需 source 一个入口即可按固化配置启动：

```bash
cd /ai/services/deepseek-v4-flash-a100/deploy
source ./start-production.sh
```

查看和停止：

```bash
./target-only/status.sh
./target-only/logs.sh --tail 200 --follow
./stop-production.sh
```

启动器允许已知的 `gpu-monitor-dcgm-exporter` 读取 GPU 监控数据，但仍会阻止真实 CUDA
计算进程、其他 GPU 容器、8005 端口冲突和公共锁。它不会自动设置 `FORCE_START=1`。

## 功能验收

```bash
./target-only/smoke-test.sh
```

目标机已得到 `13 PASS / 1 optional FAIL / 0 required FAIL`。可选失败来自字段名：该 fork
返回 `message.reasoning`，而测试最初只查找 `reasoning_content`。一次 1024-token 的复杂
推理请求以 `finish_reason=length` 结束且正文为空，说明 reasoning token 预算不能与最终
正文预算混为一谈。

## 客户端

当前优先支持 **Codex CLI + OpenAI Responses**。目标机实际使用中，Codex 能连续完成
工具调用并使用长上下文；Claude Code 经 New API 做 Anthropic/OpenAI 协议转换时曾反复
在约 7 个 token 后结束，而相同后端通过 Codex 工作正常，因此该现象记录为网关/协议
兼容性问题，不归因于 TP、KV cache 或模型上下文。

**[打开 Codex CLI 一键接入与 Claude 配置清理指南](docs/CODEX-CLI.md)**

清理脚本只移动 Claude 活动配置和凭据，完整保留 `~/.claude/projects/`、
`~/.claude/history.jsonl` 等历史，并支持恢复。

## 文档导航

- [文档中心](docs/README.md)
- [最终定型方案](docs/FINAL-CONFIGURATION.md)
- [完整离线部署](docs/DEPLOYMENT.md)
- [项目结构与源码/大文件边界](docs/PROJECT-STRUCTURE.md)
- [从旧运行包升级本项目](docs/UPGRADE.md)
- [生产运行与日常操作](docs/OPERATIONS.md)
- [测试方法与指标定义](docs/TESTING.md)
- [故障排查与现场事故记录](docs/TROUBLESHOOTING.md)
- [版本、组件和安全边界](docs/ARCHITECTURE.md)
- [横版性能汇报图（PNG）](reports/deepseek-v4-flash-a100-performance-summary-2026-08-13.png)
- [2026-08-13 A100 性能报告](reports/performance-report-2026-08-13.md)
- [可离线打开的性能报告](reports/performance-report-2026-08-13.html)
- [现场测试数据与证据等级](reports/data/README.md)
- [已知限制与未完成验收](docs/KNOWN-LIMITATIONS.md)
- [变更记录](CHANGELOG.md)

## 两个互斥模式

| 模式 | 状态 | 启动入口 | 模型名 |
|---|---|---|---|
| target-only | 生产推荐 | `source ./start-production.sh` | `deepseek-v4-flash-0731-target` |
| DSpark | 实验性 | `PROFILE=256k EXECUTION_MODE=graph ./dspark/start.sh` | `deepseek-v4-flash-0731-dspark` |

两种模式使用同一镜像、同一模型目录、同一组 8 张 GPU 和同一端口 8005，不能同时运行。
target-only 启动参数中没有 `--speculative-config`；DSpark 固定使用：

```json
{"method":"dspark","num_speculative_tokens":5,"draft_sample_method":"greedy"}
```

DSpark 不设置第二个 draft model。其 `mtp.*` 权重来自同一 0731 checkpoint。

## 固定版本

- 社区 fork：`https://github.com/haosdent/vllm.git`
- commit：`f8ea5bb163c161ef38b401d055cc5fd4a934091a`
- vLLM：`0.1.dev1+gf8ea5bb16`
- Torch：`2.13.0+cu130`
- CUDA runtime：13.0
- Triton：3.7.1
- NCCL 元数据：基础镜像 2.28.3-1；Python wheel `nvidia-nccl-cu13==2.29.7`；
  实际加载版本由目标机 `reports/runtime-versions.txt` 的 `torch_cuda_nccl` 确认
- 镜像：`dsv4-a100-vllm:f8ea5bb`
- 镜像 ID：`sha256:eb8f80df61d3124c981a1354aa0a432a3b6b7453ae588a317fa80b61e64a6159`

这是社区非官方实现。不能用 stock vLLM、新 HEAD 或目标机已有的 CUDA 12.9 nightly
镜像替代。构建机无 GPU，因此编译通过与目标机推理通过被分别记录；现在已有 A100 现场
结果，但不应把单次 benchmark 当作 24 小时稳定性证明。

## 开源致谢

本项目建立在 DeepSeek、haosdent 的 vLLM 社区分支、vLLM 上游以及 NVIDIA、PyTorch、
Triton、Hugging Face 等开源生态之上。精确仓库、固定 commit、许可证位置和第三方声明见
[THIRD_PARTY.md](THIRD_PARTY.md)。感谢所有上游维护者和贡献者。

## 模型文件

目标路径：

```bash
MODEL_DIR=/ai/models/deepseek-v4-flash-0731-modelscope/
```

ModelScope 权重本身缺少 `encoding/` 和 `inference/`。现场使用单独保存的官方辅助文件
`deepseek-v4-flash-0731-official-aux-7872f01.tar.xz` 补齐，不能联网临时获取。最终验证为：

- 48/48 safetensors shard；
- 72,317 个 index key 与 header key；
- `DeepseekV4ForCausalLM` / `deepseek_v4`；
- 目录 166,898,658,872 bytes（155.44 GiB）；
- `mtp.*` 权重存在；
- `FILE_INTEGRITY=PASS`、`DSPARK_WEIGHTS=PASS`。

文件完整性通过不等于推理正确性；后者由 smoke、benchmark 和稳定性测试分别验证。

## 离线与回滚

运行镜像已经编译完成，不需要目标机重新编译。容器设置 Hugging Face、Transformers 和
Datasets 离线变量，模型只读挂载。真正断网验收使用：

```bash
./scripts/offline_inference_test.sh target-only
./scripts/offline_inference_test.sh dspark
```

卸载只作用于本项目带 ownership label 的精确容器和镜像：

```bash
./scripts/uninstall.sh
```

模型、日志、MiniMax 和其他服务不会被删除。
