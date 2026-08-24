# deepseek-v4-flash-a100

DeepSeek-V4-Flash-0731 在 NVIDIA A100 上的离线 vLLM 部署、测试与运维项目。

当前 `main` 对应 **2026.08.20-r1**。这一版从早期 `f8ea5bb` 现场版本升级到
`haosdent/vllm@12810046c799cbe874967e19b1c0fa134ab7b209`，提供两个互斥的
target-only 方案：8×A100 / TP8 和 4×A100 / TP4。两者都固定 256K 上下文，
不启用 speculative decoding、DSpark 或 MTP。

## R1 更新摘要

- 新增 8 卡方案一（GPU 0–7、TP8、`max-num-seqs=32`）和 4 卡方案二
  （GPU 4–7、TP4、`max-num-seqs=16`）；
- API 只发布到宿主机 loopback 与 Docker 默认桥接网关，不向局域网开放；
- 固化 DeepSeek V4/V3.2 tokenizer、parser、post-load 与 Claude Code 请求转换补丁；
- 增加 500 请求稳定性门禁，以及 320 格 / 160 格可续跑性能矩阵；
- 增加可自包含打开的 HTML 报告、结构化 artifact 和失败证据收集；
- 目标机只校验并加载预编译镜像，不编译、不下载依赖，也不修改 NVIDIA Driver；
- 可识别并安全停止本项目最早版容器，但不会删除旧容器、镜像、目录或运行证据。

完整设计与兼容边界见 [R1 项目说明](r1/README.md)、
[部署说明](r1/docs/DEPLOYMENT.md) 和 [项目约束](r1/docs/PROJECT-SPEC.md)。

## GitHub 公开范围

本仓库仍是 **source-only** 发行，只公开部署代码、配置模板、测试、文档、
固定版本清单、脱敏验证材料和八个经审计的 vLLM overlay 文件。以下内容不会进入 Git：

- DeepSeek 模型权重及 `encoding/`、`inference/` 辅助文件；
- 预编译 Docker 镜像和 `r1/images/dsv4-a100-r1-images.tar`；
- 固定 base 源码压缩包、pytest wheelhouse 与其他构建产物；
- `r1/config/secrets.env`、机器配置、运行日志、PID、锁和原始现场结果。

因此，**仅克隆 GitHub 仓库不能直接完成断网部署**。完整离线交付包会另外包含
校验过的三张 Docker 镜像、固定源码快照与测试 wheels；其使用入口见
[START-HERE.md](START-HERE.md)。公开仓库保留对应 checksum 和 manifest，便于核对
外部取得的 artifact 是否与本版本一致。

## 完整离线包的日常入口

在已经取得并解压完整离线包的目标机上：

```bash
./start_one.sh        # 方案一：8×A100 / TP8
./start_two.sh        # 方案二：4×A100 / TP4
./status_one.sh
./status_two.sh
./stop.sh
```

两种方案都监听宿主机 `http://127.0.0.1:8005/v1`。同机 Docker 容器在加入
`host.docker.internal:host-gateway` 后使用
`http://host.docker.internal:8005/v1`。两种方案共享端口且 GPU 重叠，不能同时运行。

完整验收与性能矩阵：

```bash
./run-tests.sh
DSV4_SCHEME=two ./run-tests.sh
./benchmark_one.sh
./benchmark_two.sh
```

如需 API key，只在目标机从模板生成未跟踪文件：

```bash
cp r1/config/secrets.env.example r1/config/secrets.env
```

## 目录结构

```text
r1/          R1 配置、构建/运行脚本、测试、manifest、报告和文档
tests/       R1 门禁使用的上游测试文件
vllm/        八个经审计的 R1 overlay 源文件
*.sh         双方案启动、状态、停止、benchmark 与报告入口
target-only/ 早期 f8ea5bb 生产方案（保留用于升级与审计）
dspark/      早期 f8ea5bb 实验方案（不属于 R1 生产路径）
common/      早期 source-only 构建清单与许可证材料
docs/        早期版本文档与迁移背景
```

R1 是独立交付，不会覆盖旧目录。Git 历史完整保留了 1.4.0 及更早版本。

## 固定版本与验证状态

| 项目 | 固定值 |
|---|---|
| Release | `2026.08.20-r1` |
| vLLM fork | `https://github.com/haosdent/vllm.git` |
| Base commit | `12810046c799cbe874967e19b1c0fa134ab7b209` |
| CUDA 架构 | SM80 / A100 |
| 最大上下文 | 262,144 tokens |
| 服务端口 | 8005 |
| 目标 Driver | 580.159.04 或兼容 CUDA 13.0 Update 3 的更高版本 |

预编译镜像、源码测试镜像、包契约、双方案脚本和报告布局已在构建侧验证。
仓库内初始性能报告明确显示方案一 `0/320`、方案二 `0/160`，因为构建机没有 A100；
在目标机跑完矩阵后才会生成真实数据，不能把空报告当作性能结论。

详细锁定信息见 [source-lock.json](r1/manifests/source-lock.json)，已完成与未完成的
验证范围见 [validation-summary.json](r1/manifests/validation-summary.json)。

## 安全与许可证

所有停止与回滚操作都要求容器名称和 ownership labels 同时匹配；不会删除模型、
其他服务或未知镜像。API 默认不暴露到局域网。

本项目采用 Apache-2.0 许可证。DeepSeek 模型、vLLM fork、PyTorch、CUDA 及其他
依赖仍受各自许可证与使用条款约束，详见 [THIRD_PARTY.md](THIRD_PARTY.md)。
