# 已知限制

- 构建机无 A100，不能在本地声称 GPU 启动、性能或 24h 已通过；这些门禁必须在目标机
  生成真实证据。
- 四个名称共享一个 engine、调度队列和 KV 池；它们是 API 分组，不是四个隔离副本。
- `max-num-seqs=16` 是调度上限，不表示 16 个 1M 请求能同时完整常驻 KV。prefix hit
  和 chunked prefill 决定实际调度形态。
- Forced 10K/20K/30K benchmark 使用 `min_tokens` 与 `ignore_eos`，用于稳定测 decode，
  不代表超长输出内容的业务质量；parser/transport 正确性另有 guardrail。
- DSpark k=7 是官方启动候选，不是未经目标机数据即可确定的最终赢家。
- Cache profile 的 selector 输出 provisional recommendation，仍需完整矩阵和 24h soak。
- 本版本不包含跨机器 API、prefix-aware 多副本路由、CPU/SSD KV offload 或 HiCache。
