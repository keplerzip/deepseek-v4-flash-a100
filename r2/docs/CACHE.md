# Prefix cache 策略

R2 保留 R1 的原生 vLLM Automatic Prefix Caching，不引入外部 KV connector、CPU/SSD
offload 或多副本路由。理由是当前只有一个 TP8 engine；prefix-aware router 没有第二个
实例可选，而外部层级缓存会增加显存、主存、传输和正确性变量。

DeepSeek V4 的 hybrid KV 布局按 256-token logical block 缓存。只有完整 block 能命中，
所以 benchmark 使用 API 最终 usage 的 `cached_tokens`，而不是按构造文本比例猜测。
每格使用独立 `cache_salt` 隔离历史污染；prime 与测量请求共享 salt，C16 请求在共享
prefix 后立刻加入唯一分支标记。

## 接纳新方案的门槛

- 真实命中率相对目标不低于 1pp；
- 与 legacy 按输入/输出/目标命中逐格配对，任一格回退不超过 1pp；
- 16/16 正确完成且输出长度精确；
- P95 TTFT 有可复现收益；
- target 与 DSpark 都通过；
- 1M profile 完整矩阵和 24h soak 无 OOM、重启或尾延迟持续恶化。

当前 `zero` 是上游默认候选；`32768` 来自上游 1M 分支缓存实验。两者都不能仅凭社区
数字直接替换现场配置，必须通过上述门禁。

SGLang HiCache、LMCache、CPU/SSD offload 暂不进入生产镜像。它们可作为后续独立
实验，但不能与本次 native-cache 基线混测。
