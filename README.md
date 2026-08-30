# deepseek-v4-flash-a100

DeepSeek-V4-Flash-0731 在 8×A100-SXM4-80GB 上的离线 vLLM 部署、缓存与
DSpark 验证项目。当前发行版为 **2026.08.30-r2.3**；R1 与更早现场版本仍完整
保留在仓库历史和 `r1/` 中，但根目录入口已经切换到 R2。

## R2 结论先行

- 只保留 8 卡 / TP8，服务调度上限统一为 `max-num-seqs=16`；
- 方案一是 target，方案二是官方推荐的 DSpark greedy k=7；
- 一套权重、一个进程和一个 KV 池同时提供四个名称：
  `deepseek-v4-flash`、`deepseek-v4-flash[1M]`、
  `deepseek-v4-flash-claude`、`deepseek-v4-flash-claude[1M]`；
- 引擎物理上限为 1,048,576 token，不带 `[1M]` 的两个名称由服务端真实限制为
  262,144 token，不会加载四份模型；
- API 只发布到宿主回环和 Docker 默认 bridge gateway，同机容器可访问，局域网
  其他机器不可直接访问；
- Prefix cache 默认采用当前上游 `retention=0` 策略，但必须与 legacy、32768
  在同口径门禁中比较，命中率回退超过 1 个百分点即拒绝；
- 完整性能矩阵为 5 个输入长度 × 3 个输出长度 × 4 个命中率，共 60 格，每格 C16；
- 另有 DSpark k=1/3/5/7 的 9 格筛选，以及 24 小时稳定性门禁；
- vLLM/CUDA 产物全部在构建机以 `MAX_JOBS=8`、`NVCC_THREADS=1` 编译为 SM80
  镜像。Ubuntu 22.04 目标机只需
  `sudo -n docker`，不联网、不编译、不安装 Python 依赖。

R2.3 方案二（DSpark greedy k=7）已在目标机确认完整正确运行。现场实测
decode TPS 典型为 `130–180 tokens/s`，观测到的最高值为 `355 tokens/s`；峰值
不代表持续或聚合吞吐。引擎日志给出完整 1,048,576-token 请求的 KV 容量
并发为 `6.95x`，Codex Responses 正常。详细横向结论仍以固定请求形状的
60 格矩阵为准。

## 完整离线包入口

```bash
./start_one.sh                 # 方案一：target / TP8 / C16
./start_two.sh                 # 方案二：DSpark k=7 / TP8 / C16
./status_one.sh
./status_two.sh
./stop.sh

./run-tests.sh                 # 静态包契约 + API/四名称/Claude/cache 验收
./benchmark_one.sh             # target 完整 60 格
./benchmark_two.sh             # DSpark k=7 完整 60 格
./benchmark_cache_profiles.sh  # legacy / 0 / 32768 缓存策略门禁
./benchmark_dspark_k.sh        # k=1/3/5/7 分阶段筛选
./report_one.sh                # 幂等启动 target 报告服务器
./report_two.sh                # 幂等启动 DSpark 报告服务器
```

已经加载精确 `2026.08.26-r2` 镜像的目标机不必重新传输完整镜像，可使用 R2.3
增量包执行：

```bash
./update-from-r2.sh one        # 断网构建小型修复层并启动 target
./update-from-r2.sh two        # 断网构建小型修复层并启动 DSpark k=7
```

增量安装器要求旧镜像 ID 精确匹配，不下载依赖、不运行 pip，也不重新编译 CUDA。

模型仍使用目标机原目录，不进入交付包：

```text
/ai/models/deepseek-v4-flash-0731-modelscope
```

宿主机 API 是 `http://127.0.0.1:8005/v1`。同机 Docker 客户端加入
`--add-host host.docker.internal:host-gateway` 后使用
`http://host.docker.internal:8005/v1`。服务容器内虽监听 `0.0.0.0`，宿主机绝不
发布 `0.0.0.0:8005`，因此不会把 API 暴露给整个局域网。

## GitHub 与完整离线包的区别

GitHub 是 source-only 视图，包含脚本、测试、文档、manifest 和审计记录，不包含：

- 模型权重；
- `r2/images/dsv4-a100-r2-image.tar`；
- 自动生成的完整 vLLM 源码快照；
- 目标机日志、缓存、性能 CSV、API key 或其他秘密。

所以仅克隆 GitHub 不能在全新断网目标机直接部署。构建侧执行
`r2/scripts/package_offline_release.sh` 后生成的单一压缩包才包含预编译运行环境和
精确源码快照；已有精确 R2 基础镜像时，也可执行
`r2/scripts/package_incremental_release.sh` 生成小型增量交付。

## 版本与证据

| 项目 | 固定值 |
|---|---|
| Release | `2026.08.30-r2.3` |
| vLLM source | `cf7898691b58820a8ba98e018f612d4a0c2f69f0` |
| CUDA 架构 | SM80 / A100 |
| GPU / TP | 8 / 8 |
| Engine context | 1,048,576 |
| Scheduler | C16 |
| 服务端口 | 8005 |
| 默认缓存 profile | `zero`（需目标机 A/B/C 与 24h 门禁） |

源代码选择见 [上游审计](r2/docs/UPSTREAM-AUDIT.md)，部署见
[R2 说明](r2/README.md)，本机构建证据见
[build-validation.json](r2/manifests/build-validation.json)，测试口径见
[测试文档](r2/docs/TESTING.md)。构建机没有
A100，因此构建阶段不预填性能数字。仓库中的性能记录均来自目标机现场
实测；完整矩阵结论以目标机生成的 CSV/JSON/HTML 为准。

本项目采用 Apache-2.0。模型和各依赖仍受各自许可证与条款约束。
