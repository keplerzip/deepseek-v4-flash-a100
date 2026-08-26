# Changelog

## 2026.08.26-r2

- 方案收敛为 8×A100/TP8/C16：target 与 DSpark greedy k=7；
- 单 engine 公布四个 256K/1M、OpenAI/Claude 名称，并新增真实 per-alias context gate；
- 引擎统一开启 1,048,576 上下文，模型权重继续使用目标机原目录；
- 合入 13 个 DSV4、DSpark、hybrid prefix cache、CUDA graph、tokenizer 和 KV 容量修复；
- 使用 `MAX_JOBS=8`、`NVCC_THREADS=1` 完整重编译 SM80 镜像，目标 Ubuntu 22.04
  仅加载镜像，不编译或联网；
- 保留宿主 loopback + Docker bridge gateway，继续禁止 LAN 发布；
- 新增 60 格 C16 长上下文矩阵、cache legacy/0/32768 门禁、DSpark k=1/3/5/7
  筛选、四名称/Claude API 验收与 24 小时 soak；
- Prefix cache 候选必须逐格保持 legacy 命中率在 1pp 内，报告空值不伪装为 0；
- 报告服务器改为幂等，已运行时返回现有 URL。

## 2026.08.20-r1 — 2026-08-24 source publication

- 升级固定 base 到 `haosdent/vllm@12810046c799cbe874967e19b1c0fa134ab7b209`；
- 新增 8×A100/TP8 与 4×A100/TP4 两个互斥的 256K target-only 方案；
- 加入 tokenizer、parser、post-load 和 Claude Code 请求转换的经审计 overlay；
- API 发布范围收紧到宿主机 loopback 与 Docker 默认桥接网关；
- 新增 500 请求稳定性门禁、320/160 格性能矩阵和自包含报告；
- 新增完整离线包的镜像校验、加载、回滚、旧版安全收敛与归档热修复流程；
- GitHub 继续保持 source-only：不提交模型、镜像、固定源码 tar、wheels、密钥或日志。

## 1.4.0-f8ea5bb-structure — 2026-08-18

- 清理根目录，将 Codex 指南、已知限制、checksum 说明和源码更新清单归入职责目录；
- 新增统一文档中心和独立的最终定型方案页；
- 为 `config/`、`common/`、`scripts/`、两个运行模式和 `reports/` 增加目录索引；
- 保留所有已验证的生产入口与模式脚本路径，避免目录优化破坏目标机命令；
- 更新构建失败记录、checksum、升级备份和文档的全部新路径。

## 1.3.0-f8ea5bb-public — 2026-08-18

- 以 `deepseek-v4-flash-a100` 名称整理 source-only 公开仓库；
- README 首页直接披露 A100 target-only、DSpark、长上下文和 batched-token 调优实测；
- 新增项目结构说明，明确源码、运行时目录和离线大文件边界；
- 新增 Apache-2.0 根许可证和完整第三方开源项目致谢；
- 排除模型、镜像、wheels、编译缓存、vendored checkout、机器配置和原始日志；
- 对公开 Git 跟踪集执行敏感词、账号、内网身份、私钥与 Token 扫描。

## 1.2.0-f8ea5bb-a100-field — 2026-08-16

- 将 target-only 设为生产推荐模式；
- 新增 `source ./start-production.sh` 单入口；
- 固化 256K、`max_num_seqs=16`、`max_num_batched_tokens=4096`、0.92、CUDA Graph；
- 256K DSpark 改为并发 6、默认显存比例 0.80，并明确标记实验性；
- 为 `max_num_batched_tokens` 和 `kv_cache_memory_bytes` 增加一等配置项，避免把 CLI
  参数直接写入可 source 的 env 文件；
- GPU guard 增加精确容器 allowlist，允许 DCGM exporter，但继续阻止真实计算进程；
- 保留 Docker 29 的 `--gpus all` 兼容修复；
- 新增完整部署、运维、测试、架构、故障排查和性能报告；
- 保存 2026-08-13 A100 现场 benchmark、GPU 与 DSpark acceptance 结构化摘录；
- 将 Codex CLI + Responses 提升为优先客户端，记录 Claude/New API 转换中断问题；
- 版本从 `1.1.0-f8ea5bb-runtime` 更新为 `1.2.0-f8ea5bb-a100-field`。

## 1.1.0-f8ea5bb-runtime — 2026-08-13

- 在无 GPU、32GiB 构建机上用 8 jobs 完成固定 commit 的 SM80 编译；
- 生成可离线导入的公共 runtime image；
- 添加 target-only 与 DSpark 双模式、GPU guard、smoke、benchmark、stability 和回滚脚本；
- 添加 ModelScope 权重 metadata 与 DSpark 权重检查；
- 完成 Ubuntu 22.04 / CUDA 13 / Driver 兼容性审计。
