# R2 测试与决策口径

## KPI 层级

主 KPI 只保留三项：

1. P95 TTFT：客户端发出请求至第一个有效流式 delta；
2. 有效未命中 Prefill TPS：`(prompt_tokens - cached_tokens) / TTFT`；
3. 聚合 Decode TPS：C16 各请求首 token 之后的成功 token，除以最早首 token 到最晚
   完成之间的并发 decode 窗口；不会把长上下文 prefill 整段混入 decode 指标。

第二项包含排队和首个 decode token 的开销，只用于端到端方案比较，不宣称是纯
prefill kernel TPS。

驱动指标：实际缓存命中率、ITL、DSpark acceptance。护栏：16/16 请求成功、精确
输出长度、无 parser 原始标记、无 OOM/illegal memory/engine restart、命中率短缺不
超过 1 个百分点。

目标采用基线加改进法：legacy 是缓存基线，target 是 decode/TTFT 基线。构建机无
A100，因此不预设虚假的绝对 TPS 目标；R2 候选必须不劣于基线护栏，再比较相对收益。

## 分阶段执行

### 0. 静态与小请求验收

```bash
./run-tests.sh
```

验证包结构、60 格计划、四个模型名称及 max length、OpenAI API、Anthropic API、
256K alias 对超限请求返回 HTTP 400，以及一次温缓存命中。

### 1. Cache A/B/C

```bash
./benchmark_cache_profiles.sh
```

每个 profile 跑 200K/600K/1M × 80%/90%/95% × C16，输出 256 token。此阶段聚焦
cache 与 TTFT，避免把大量 decode 时间掺入缓存策略选择。候选与 legacy 按格配对，
任一格回退超过 1pp 即淘汰；选择器输出 provisional recommendation，不自动改配置。
门禁结束后会停止最后一个临时 profile，避免误把 `32768` 留成正在运行的生产服务。

### 2. DSpark k 筛选

```bash
./benchmark_dspark_k.sh
```

k=1/3/5/7 逐一真实启动。每档跑 200K/600K/1M × 10K/20K/30K × 90% × C16，
共 9 格。启动失败会写入 `status.tsv`，不会静默跳过。selector 只给 decode leader，
最终 k 等待用户结合 TTFT、acceptance、cache 和稳定性确认。

### 3. 两方案完整矩阵

```bash
./benchmark_one.sh
./benchmark_two.sh
```

默认可续跑。查看：

```bash
./report_one.sh
./report_two.sh
```

### 4. 24 小时稳定性

```bash
DSV4_SCHEME=target r2/scripts/run_stability.sh --duration-hours 24
DSV4_SCHEME=dspark r2/scripts/run_stability.sh --duration-hours 24
```

soak 使用 600K 输入、1K 输出、90% 命中、C16，持续记录每 wave、GPU 显存/利用率、
容器 restart/OOM 和服务日志。任一 wave 命中率低于目标 1pp 或请求失败即失败。

## 结果位置

所有目标侧证据在 `/var/tmp/dsv4-a100-r2.1-20260830`，按 target 与 dspark-k 分开。
CSV 的 pending/null 不会在 HTML 中显示为 0。
