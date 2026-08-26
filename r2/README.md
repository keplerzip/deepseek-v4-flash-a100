# DeepSeek V4 Flash A100 R2

R2 是从 R1 独立升级的 8×A100 离线交付。它在构建机以 `MAX_JOBS=8`、
`NVCC_THREADS=1` 完整重编译 vLLM/CUDA SM80 产物，目标机只加载镜像。R1 目录、
旧镜像和旧运行证据不会被覆盖。

## 两个互斥方案

| 入口 | Engine | GPU / TP | max-num-seqs | max-model-len | Spec decode |
|---|---|---:|---:|---:|---|
| `./start_one.sh` | target | 8 / 8 | 16 | 1,048,576 | 无 |
| `./start_two.sh` | DSpark | 8 / 8 | 16 | 1,048,576 | greedy k=7 |

两个方案均公开四个 alias，共用一份权重和 KV cache。256K alias 的限制在 prompt
渲染和 token 计数之后、请求进入 engine 之前执行；它不是前端文字约定。

## Prefix cache

默认 profile 是 `zero`，对应当前上游的 retention interval 0。以下 profile 均保留：

| Profile | 容器环境 | 用途 |
|---|---|---|
| `legacy` | 不设置变量 | R1 行为基线 |
| `zero` | `VLLM_PREFIX_CACHE_RETENTION_INTERVAL=0` | 当前上游候选 |
| `32768` | `...=32768` | 超长分支 checkpoint 候选 |

`./benchmark_cache_profiles.sh` 会重启同一 target 配置做 A/B/C。候选实际命中率相对
目标或逐格配对的 legacy 回退超过 1 个百分点即淘汰；暂定优胜者仍需跑完整 60 格与
24h soak。

## Benchmark

`benchmark_one.sh` 与 `benchmark_two.sh` 的默认矩阵：

- 输入：200K、400K、600K、800K、1000K；
- 输出：10K、20K、30K；
- 目标缓存命中：80%、85%、90%、95%；
- 并发：C16；
- 内容：代码维护场景，含中文注释；
- 共 60 格、960 个测量请求，另有每格专用 prefix prime；
- 流式采集 TTFT、ITL、decode TPS、有效未命中 prefill TPS；
- 使用最终 API usage 的 `cached_tokens / prompt_tokens` 判定真实命中。

CSV、summary JSON 和自包含 HTML 位于：

```text
/var/tmp/dsv4-a100-r2-20260826/<target|dspark-k7>/results/benchmark/
```

每格结束后原子保存，重跑会跳过完成格；失败格需显式加 `--rerun-failed`。

## 目标权限与网络

脚本优先使用当前用户 Docker；不可用时自动切换为 `sudo -n docker`。不依赖宿主
Python、pip、Node 或编译器。

API 只发布至：

```text
127.0.0.1:8005
<Docker default bridge gateway>:8005
```

Docker 客户端必须显式加入 `host.docker.internal:host-gateway`。默认没有宿主 LAN
地址或 `0.0.0.0:8005` 发布。

更多内容：

- [部署](docs/DEPLOYMENT.md)
- [测试与 KPI](docs/TESTING.md)
- [缓存决策](docs/CACHE.md)
- [上游更新审计](docs/UPSTREAM-AUDIT.md)
- [本机构建验证证据](manifests/build-validation.json)
- [已知限制](docs/KNOWN-LIMITATIONS.md)
