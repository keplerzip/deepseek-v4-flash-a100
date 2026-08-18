# 已知限制与验证边界

当前固化参数与配置优先级见 [最终定型方案](FINAL-CONFIGURATION.md)。本页只记录仍未闭环
的验收、实验模式事故和证据边界。

截至 2026-08-16，固定 runtime 已在目标 A100 机器运行，模型完整性、target-only 必需
smoke、CUDA Graph、300K 单路、C12 短请求、DSpark 加载与 acceptance 均已有现场证据。
以下项目仍不能宣称通过。

## 高优先级

### 最终生产组合尚缺完整稳定性测试

最终固化为 target-only、256K、C16、4096、0.92、Graph。选择依据包括 300K/C1、
307K/C12 短请求和 512K batched-token 调优，但尚未保存这个最终组合的：

- C1/2/4/8/16 同口径短请求矩阵；
- 逐级长上下文并发；
- 1 小时稳定性；
- 24 小时 soak；
- 重启恢复后的重复性能。

在完成这些测试前，“生产推荐”表示当前风险最低的配置，不表示已经获得 24 小时 SLA。

### DSpark CUDA Graph 长上下文崩溃

一个 256K、手工 16GiB KV、Graph 的长上下文并发测试触发
`CUDA illegal memory access` 并导致多 rank 退出。相似 C3 在 eager 下通过，0.80/C6 的另
一次 Graph 矩阵也通过，但配置并不相同，不能相互抵消。DSpark 当前为实验模式。

### 现场原始结果尚未归档

本项目保存的是操作者粘贴的精确终端输出与结果文件名，证据等级 B。目标机上的原始
benchmark JSON、request rows、GPU CSV、launch manifest 和完整 runtime logs 尚未复制
回来，无法重新计算 percentile、逐请求错误或完整时间序列。

## 中优先级

### Reasoning 字段兼容

该 fork 的 chat response 出现 `message.reasoning`，而部分 OpenAI 兼容客户端只读取
`reasoning_content`。复杂推理还可能在 final content 前耗尽 `max_tokens`。需要对 Codex、
New API 和其他客户端做端到端字段映射测试。

### Claude Code 经 New API 转换中断

现场大量 Claude 请求约 7 token 后结束，而 Codex/Responses 使用相同模型正常。问题更
可能位于 Anthropic/OpenAI 的 stop、tool result 或 stream 转换。当前没有完整代理 trace，
Claude 转换路径不列为受支持生产客户端。

### GPU0 平均利用率低于其他 rank

一次 C12 活跃采样中 GPU0 平均 74.2%，GPU1–7 平均约 97.3%；显存分片和峰值正常。
需要更长采样并关联 rank0/API 调度、NUMA 和监控误差后才能判断是否可优化。

## 低优先级与已知限制

### 原生 wheelhouse

`common/wheelhouse/WHEELHOUSE_COMPLETE` 不存在。受支持主方案是已编译的离线容器镜像；
不要把 wheelhouse 提示当成失败，也不要声称原生安装可用。

### DSpark draft latency

该 commit 暴露 draft、draft tokens 和 accepted tokens counter，但没有直接 draft latency
指标。报告保留 null，不进行推测。

### NCCL 实际加载版本待重新采集

基础镜像声明 2.28.3-1，Python freeze 为 2.29.7，构建日志出现过 2.30.7 override 意图。
现有目标机摘录没有 `torch.cuda.nccl.version()`。更新后的 `verify_image.sh` 会把实际加载值
写入 `reports/runtime-versions.txt`；下次目标机运行后再关闭此项。

### MiniMax 严格 A/B

历史 MiniMax-M2.7 基线为 230B total / 10B activated、约 11K 输入、TTFT 约 15 秒。
原始 11K prompt 未找到，因此尚不能做严格同 prompt A/B。

## 已关闭的历史阻塞

- 固定 commit 已在无网络容器内以 8 jobs 编译成功；
- SM80 cubin、wheel 安装、CLI 与 DSpark 源码能力验证通过；
- Driver 580.159.04 满足项目检查的 CUDA 13.0 最低版本；
- ModelScope 缺少的 `encoding/` 和 `inference/` 已用离线官方辅助包补齐；
- Docker 29 `Count/DeviceIDs` 冲突已用 `--gpus all` 修复；
- DCGM exporter 误拦截已用精确 allowlist 处理；
- New API 容器连接问题已通过绑定 Docker bridge 地址解决；
- target-only 与 DSpark 均实际加载并提供 API，DSpark 不是静默退化为 target-only。
