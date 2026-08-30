# 2026-08-26 上游更新审计与 R2.1 兼容修复

审计基于 `vllm-project/vllm` 当日 main，针对 DeepSeek-V4-Flash-0731、A100 SM80、
TP8、1M、prefix cache 和 DSpark。R2 source 锁定为
`bc51bfa7903de8cb94144fbab0aac1e6b333e6b6`。

## 已纳入

| PR / commit | 作用 |
|---|---|
| #51843 `e6707e5b65` | 禁止不兼容 hybrid KV layout 的细粒度 prefix hit |
| #51538 `d3971f892f` | DSV4 sparse MLA 在 target/MTP/DSpark 端到端修复 |
| #52288 `baafee96a3` | DSpark 继承 target attention backend |
| #52401 `6e71ad63ca` | 按 model runner 选择 eager CUDA graph region |
| #52084 `ddaf035234` | sparse top-k metadata prefill 优化 |
| #51967 `65d5160076` | global top-k 编译常量优化 |
| #52492 `a842bc79d3` | indexer scoring 保留在 breakable graph |
| #52836 `0144461849` | 回退跨 stream 共享 eager workspace，避免竞态 |
| #53071 `9b65ef21fb` | 未知 chat role 返回 HTTP 400 |
| #52809 `8ee8b0c889` | DSpark backend 继承限定到 DSV4 |
| #51262 `2d136ac44f` | trailing system message / Claude Code 修复 |
| #53747 `700c52b684` | tokenizer assert 改为可诊断 ValueError |
| #47272 `1caeaca28a` | 1M max length 校验预留 KV null block |

此外保留 R1 tokenizer/parser/Claude 兼容补丁，增加四 alias 的 per-name context limit，
并修正 Docker 构建上下文对上游 `.git` BuildKit bind mount 的排除冲突。

R2.1 另修复 #51538 在本分支的回移依赖错位：本 A100 分支明确没有接纳 #51718
的大规模 KV layout 重构，因此 `flashinfer_sparse.py` 必须继承该基线真实存在的
`DeepseekV4FlashMLABackend`。原 R2 镜像错误引用仅在 #51718 之后才存在的
`DeepseekV4SparseMLABackend`，导致 target 和 DSpark 都在模型注册时退出。修复只对齐
Python 后端接口，不改变 A100 SM80 实际选择的 FlashMLA kernel、权重或 KV 格式。

## 配置接纳、未直接 cherry-pick

- #52216 把 prefix cache retention 提升为 CLI 并将默认值改为 0。R2 分支已有同功能
  环境变量，为降低 17 文件配置重构冲突，运行脚本显式设置 0，并保留 legacy/32768。
- #43447 的缓存保留实验报告在 14 个并发 1M 请求下取得超过 95% prefix hit；R2 只把
  32768 当 A/B 候选，不直接当结论。

## 暂缓

- #52823 自适应 top-k width：短上下文 kernel 收益明显，但合并时的有效端到端验证最长
  prompt+completion 约 9K，未覆盖本项目 200K–1M，暂不冒正确性风险；
- #52217 sparse MLA mask 128-bit vector load：是潜在 decode 优化，但与 A100 fork 同文件
  已有较大分叉，且没有 200K–1M/A100 数据；留作矩阵之后的单变量实验，不混入本版；
- #52789 的 9%–25% TTFT 数据来自 Kimi-K3 的 Mamba/FlashKDA internal checkpoint，
  不是 DeepSeek V4 sparse MLA 路径，不能把该数字外推到本模型；
- #52626 与 #51368 分别修 RL 在线 weight sync 和 dummy weight loading；本部署只做一次
  静态 safetensors 加载，不执行 refit/dummy load，因此不扩大模型加载改动面；
- #53456 修 HunyuanOCR 等多模态 XD-RoPE 的缓存命中 metadata，本模型是纯文本 DSV4，
  不经过该路径；#52436 只修 adaptive draft budget 为 0 时的 structured-output grammar，
  本方案固定 greedy k=1/3/5/7；
- #51718 大规模 KV layout 重构：改动面过大，会覆盖已验证 A100 fork；
- FlashInfer MoE EP、SM90/SM100 adaptive verification：当前 TP8 A100 方案不适用；
- SGLang HiCache：虽支持 CPU/SSD 层级缓存，但同期仍有 DSV4+DSpark 长 prefix stall、
  segfault/错误输出问题；
- 外部 KV offload：已有 DSpark cache stuck-at-zero 报告，本版本不加入。

## 主要资料

- vLLM APC：<https://docs.vllm.ai/en/stable/design/prefix_caching/>
- vLLM DeepSeek V4：<https://github.com/vllm-project/vllm-project.github.io/blob/main/_posts/2026-04-24-deepseek-v4.md>
- retention queue #43447：<https://github.com/vllm-project/vllm/pull/43447>
- retention CLI/default #52216：<https://github.com/vllm-project/vllm/pull/52216>
- eager workspace race #52836：<https://github.com/vllm-project/vllm/pull/52836>
- adaptive top-k #52823：<https://github.com/vllm-project/vllm/pull/52823>
- sparse MLA vector load #52217：<https://github.com/vllm-project/vllm/pull/52217>
- Kimi-K3 internal checkpoint #52789：<https://github.com/vllm-project/vllm/pull/52789>
- DSV4 weight-sync buffer #52626：<https://github.com/vllm-project/vllm/pull/52626>
- 官方 0731 model card（含 DSpark k=7 命令）：<https://huggingface.co/deepseek-ai/DeepSeek-V4-Flash-0731>
- SGLang HiCache best practices：<https://github.com/sgl-project/sglang/blob/main/docs_new/docs/advanced_features/hicache_best_practices.mdx>
