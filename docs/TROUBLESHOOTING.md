# 故障排查与现场事故记录

## ModelScope 模型缺少 `encoding/` 和 `inference/`

现象：

```text
FILE_INTEGRITY=FAIL
missing required directory: encoding/
missing required directory: inference/
```

原因：ModelScope 下载的 48 个权重 shard 完整，但仓库快照没有 DeepSeek V4 官方辅助
目录。使用单独保存且校验过的
`deepseek-v4-flash-0731-official-aux-7872f01.tar.xz` 补齐，不能运行时联网获取。步骤见
[部署文档](DEPLOYMENT.md)。

## GPU guard 被 DCGM exporter 阻止

现象：

```text
BLOCK: running GPU container: gpu-monitor-dcgm-exporter ...
```

原因：DCGM exporter 请求 GPU device，但通常没有 CUDA compute process。项目现在支持
精确 allowlist：

```bash
GPU_GUARD_ALLOWED_CONTAINERS=gpu-monitor-dcgm-exporter
```

这不会放过 `nvidia-smi` 中的计算进程，也不会允许其他 GPU 容器。不要因此全局设置
`FORCE_START=1`。

## Docker 29 报 Count 与 DeviceIDs 冲突

现象：

```text
docker: Error response from daemon: cannot set both Count and DeviceIDs on device request
```

项目已将 Docker 参数改为 `--gpus all`，再用
`CUDA_VISIBLE_DEVICES=0,1,2,3,4,5,6,7` 固定卡序。不要恢复为未正确引用的逗号设备列表。

旧失败可能留下 `Created` 状态容器。下一次启动只会删除具有本项目 ownership label 的
同名停止容器，不会触碰其他容器。

## New API 从容器访问 `127.0.0.1` 被拒绝

现象：

```text
Get "http://host.docker.internal:8005/v1/models": connect: connection refused
```

如果 vLLM 只绑定 `127.0.0.1`，New API 容器连接的 host gateway 不是同一个 loopback。
生产入口绑定 `172.17.0.1:8005`。同时给 New API 容器添加：

```yaml
extra_hosts:
  - "host.docker.internal:host-gateway"
```

宿主机先用 `curl http://172.17.0.1:8005/v1/models` 验证，再配置网关。

## 启动等待期间持续 connection refused

DeepSeek V4 需要加载 48 个 shard、初始化 8 个 worker、编译和捕获 Graph。现场 DSpark
约等待 3 分钟后才 ready。只要容器仍运行且日志持续前进，等待中的 curl 失败不是故障。

检查：

```bash
sudo -n docker ps -a --filter name=dsv4
./target-only/logs.sh --tail 300
./dspark/logs.sh --tail 300
```

## eager 模式只有约 7–8 tok/s

现场 target-only 32K eager 的 1K/11K 单路 decode 分别约 7.89/7.29 tok/s。eager 是
首次功能排障模式，会禁用 CUDA Graph，不能用于性能结论。生产使用 graph；切换模式必须
停止并重新启动容器。

## DSpark 在 0.95 发生 OOM

现场 256K、DSpark、0.95 下，1K、11K、128K 单路完成，但 260K/512 申请约 382MiB
临时显存时每卡只剩约 9MiB，随后 API shutdown。`gpu_memory_utilization` 主要规划模型和
KV cache，不会保证 speculative workspace、Graph、NCCL 和临时张量仍有空间。

DSpark 默认降为 0.80。不要用 0.95 追求“显存看起来满”。

## DSpark CUDA Graph 出现 illegal memory access

现场在 256K、手工 `kv_cache_memory_bytes=17179869184`、长 prompt 并发增加时发生：

```text
CUDA error: an illegal memory access was encountered
c10::AcceleratorError
```

这是引擎级崩溃，发生后必须停止并重启容器；不能把剩余请求结果当作稳定通过。相同
260K/C3 在 eager 下 3/3 完成，说明问题与 Graph/长上下文/并发组合相关，而不只是模型
文件损坏。生产不使用该手工 KV 值。

若需要上游定位，可单独设置 `CUDA_LAUNCH_BLOCKING=1` 复现，但该设置会显著影响性能，
不用于 benchmark。

## TileLang vectorization warning

现象：

```text
T.vectorized loop over `i_hci` with extent 4 is lowered as a serial loop
```

这是 kernel 编译器未找到合法向量化方案后的回退告警。它可能损失该小循环的性能，但
不是服务失败：现场随后完成 CUDA Graph capture、API startup 和多个 benchmark。除非
正在开发 kernel，不要直接修改生成代码；用端到端 A/B 判断实际影响。

## `max_num_scheduled_tokens=2032` 警告

DSpark 会为每个 target token 额外安排 draft slot，fork 因 speculative 配置把调度上限
设为 2032 并提示可能非最优。target-only 的 4096 调优结果不能直接复制给 DSpark；两种
模式的 Graph 内存与调度开销不同。当前 DSpark 让 fork 自动推导，等待独立矩阵再固化。

## env 文件报 `command not found`

旧调优曾把：

```text
--enable-chunked-prefill
```

直接放入 `config/target-only.env`，source 时被 shell 当作命令。当前代码使用：

```bash
MAX_NUM_BATCHED_TOKENS=4096
KV_CACHE_MEMORY_BYTES=
```

并由 `mode_action.sh` 生成 CLI 参数。不要在 env 文件中放裸参数。

## reasoning 正文为空

一次复杂 12 球问题使用 `reasoning_effort=max`、`max_tokens=1024`，返回：

```text
finish_reason=length
content_length=0
message keys 包含 reasoning，但不含 reasoning_content
```

模型把 token 预算耗在 reasoning 阶段，尚未进入 final content。提高输出预算并让客户端
读取该 fork 的 `reasoning` 字段；不要据此判断模型卡死。API smoke 中该项是 optional。

## Claude Code 经 New API 经常只输出约 7 token

同一后端通过 Codex CLI 能完成连续工具调用和长上下文，说明服务端上下文、TP 和 KV
不是固定 7-token 中断的直接原因。优先检查 New API 的 Anthropic/OpenAI 转换、stop
sequence、tool result 映射、stream 结束事件和客户端超时。当前推荐 Codex + Responses。

## Graph 与功耗看起来偏低

A100 的功耗受 kernel 类型、MoE 激活、通信和时钟限制影响；显存占满不等于计算单元满载。
现场 target-only C12 时 GPU1–7 平均利用率约 97.3%，但平均功耗约 167W，瞬时峰值约
300–311W。这可以是短采样和 workload 形态的结果，不能只凭功耗判定 TP 没工作。
